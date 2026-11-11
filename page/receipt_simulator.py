# -*- coding: utf-8 -*-
"""小票模拟打印页面（参照直播间实时吐小票的玩法视频 + liveHelper 的 receipt.html）。

只做「打印机吐纸」这一块，不含任何主播/直播间元素：
- 顶部一个深色「出纸口」，纸带从下面连续滑出；
- 每条直播消息（进场/礼物/聊天/点赞/关注/粉丝团）生成一张小票，仿 58mm 热敏纸：
  暖白纸底、等宽字体、居中；礼物/玩法为多行票（抬头+票号+头像圆章+评分章+明细+红章），
  其余为单行紧凑票（文案 + 旋转红章）；
- 仿真实热敏打印机行为：最新小票贴在出纸口正下方（最上方），旧票被往下顶、从底部撕掉（FIFO）；
- 新票淡入（出纸动画），最新始终停在可见区域；超过 MAX_CARDS 自动丢弃最旧的。

数据来源：liveMain.append_message 主线程路径里把结构化消息 dict 喂给
add_event()，与打印/显示勾选互不影响。

另提供「手机预览」按钮：弹出 390×844 的独立窗口，内部小票与上方「小票模拟」
完全一致（同一套 _build_ticket 渲染、同样的红章/评分章/头像、数据同步），
仅外壳是手机尺寸，方便在电脑上预览手机端效果。
"""

import logging
import os
import random
import datetime

from PyQt5.QtCore import (Qt, QThread, pyqtSignal, pyqtSlot, QTimer, QRectF,
                          QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup,
                          QParallelAnimationGroup, QObject, pyqtProperty)
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath,
                         QPen, QPixmap, QBrush, QImage, QRadialGradient,
                         QTransform)
from PyQt5.QtMultimedia import QSound
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget,
                             QGraphicsOpacityEffect)

from core.printer_manager import (download_avatar, classify_chat_style, CHAT_STYLES)
from core.app_state import AppState
from utils.idle_filler import IdleFiller, random_interaction_event
from page.printer3d import Printer3DWindow   # 3D打印现场（主页「3D打印」按钮）

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

MAX_CARDS = 120                    # 纸卷上最多保留多少张小票（超出丢最旧）

PRINTER_HEADER = "树洞"            # 小票抬头店铺名（与 printer_manager.PRINTER_HEADER 一致）

# 热敏纸配色（取自 receipt.html）
PAPER_BG = "#f3efe3"              # 暖白纸底
INK = "#2b2b2b"                   # 正文墨色
META = "#9a938a"                  # 票号/时间
SEP = "#b9b2a6"                   # 分隔线
FOOT = "#6d675d"                  # 落款
STAMP_RED = "#d4382c"             # 红章


def _make_clock_label(style):
    """年月日时分秒 实时时钟 Label（每秒刷新），样式由调用方给定。"""
    lbl = QLabel(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lbl.setStyleSheet(style)
    timer = QTimer(lbl)
    timer.timeout.connect(lambda: lbl.setText(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    timer.start(1000)
    return lbl

DEMO_NICKS = ["抹茶星冰乐", "霁云", "小碗宽面", "长青树", "咬一口糯米", "Jinggg", "阿白", "糖藕"]
DEMO_GIFTS = [("玫瑰", 10, 520), ("小心心", 1, 1), ("嘉年华", 1, 30000),
              ("跑车", 1, 8000), ("棒棒糖", 66, 66)]


def _mono_font(size=12, bold=False):
    f = QFont("Menlo")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(size)
    if bold:
        f.setBold(True)
    return f


# ---------------------------------------------------------------------------
# 演示数据 & 章 & 头像
# ---------------------------------------------------------------------------

def _make_demo_event():
    """生成一条随机演示事件（进场/礼物/聊天/点赞/关注），供两个页面共用。"""
    src = random.choice(["进场", "进场", "礼物", "聊天", "点赞", "关注"])
    nick = random.choice(DEMO_NICKS)
    if src == "礼物":
        gift, cnt, price = random.choice(DEMO_GIFTS)
        return {"source": "礼物", "nickName": nick, "giftName": gift,
                "giftCount": cnt, "price": price}
    if src == "聊天":
        return {"source": "聊天", "nickName": nick,
                "msg": random.choice(["早上好呀", "主播声音好听",
                                      "来了来了", "666", "表白主播，爱你哟", "今天也要元气满满"])}
    if src == "点赞":
        return {"source": "点赞", "nickName": nick,
                "goodCount": random.randint(1, 20)}
    if src == "关注":
        return {"source": "关注", "nickName": nick}
    return {"source": "进场", "nickName": nick}


# ---------------------------------------------------------------------------
# 动画辅助：平滑滚动到最新 + 新小票淡入（营造"打印吐纸"效果）
# ---------------------------------------------------------------------------

def _animated_scroll_to_bottom(scroll, follow_threshold=100):
    """平滑把纸带滚动到底部，让最新小票始终停在可见区域。

    若用户已上滑查看历史（离底部超过 follow_threshold 像素），则不打扰其阅读位置。
    """
    bar = scroll.verticalScrollBar()
    maxv = bar.maximum()
    if bar.value() < maxv - follow_threshold:
        return
    anim = getattr(scroll, "_bottom_anim", None)
    if anim is None:
        anim = QPropertyAnimation(bar, b"value", scroll)
        anim.setDuration(320)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        scroll._bottom_anim = anim
    anim.stop()
    anim.setStartValue(bar.value())
    anim.setEndValue(maxv)
    anim.start()


def _fade_in(widget, duration=260):
    """新小票淡入（配合出纸动画，像打印机把纸吐出来）。"""
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setEasingCurve(QEasingCurve.OutCubic)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.start()


# 礼物触发金龙飞出的概率（0~1）。用户刷礼物时随机飞出一条金龙，活跃直播间气氛。
GIFT_DRAGON_PROB = 0.4


# 金龙素材：优先用生成的高清金龙 assets/images/dragon_gold.png（1536x1024 黑底带辉光），
# 旧版用户素材 gift_show.png 作为后备；缺失或加载失败再回退到离屏手绘的金色小龙。
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DRAGON_IMG_CANDIDATES = [
    os.path.normpath(os.path.join(_THIS_DIR, "..", "assets", "images", "dragon_gold.png")),
    os.path.normpath(os.path.join(_THIS_DIR, "..", "assets", "images", "gift_show.png")),
]
_dragon_source_pixmap = None  # 懒加载并缓存
_dragon_pixmap_cache = {}     # (w, h) -> 抠底后的 QPixmap

# 金龙咆哮音效：放在 assets/voice/dragon_roar.wav（可从网上找的免费可商用素材）。
DRAGON_SOUND_PATH = os.path.normpath(
    os.path.join(_THIS_DIR, "..", "assets", "voice", "dragon_roar.wav")
)
_dragon_sound = None          # 懒加载的 QSound 实例


def play_dragon_roar():
    """金龙出现时播放一次咆哮音效；文件缺失或播放异常时静默跳过。"""
    global _dragon_sound
    if not os.path.exists(DRAGON_SOUND_PATH):
        return
    try:
        if _dragon_sound is None:
            _dragon_sound = QSound(DRAGON_SOUND_PATH)
        _dragon_sound.play()
    except Exception as e:
        logging.warning("[小票模拟] 金龙音效播放失败: %s", e)


def _load_dragon_source():
    """加载并缓存金龙素材图（只加载一次）。依次尝试候选路径，全部失败则返回 None。"""
    global _dragon_source_pixmap
    if _dragon_source_pixmap is not None:
        return _dragon_source_pixmap
    for path in DRAGON_IMG_CANDIDATES:
        if os.path.exists(path):
            px = QPixmap(path)
            if not px.isNull():
                _dragon_source_pixmap = px
                break
    return _dragon_source_pixmap


def _key_out_black(img):
    """素材是黑底无 alpha 的 RGB 图：按亮度抠底 —— 亮度（max(r,g,b)）即透明度，
    黑底全透明。同时按 premultiplied 反解提亮边缘（黑底上「暗色半透明」的辉光边
    还原成「亮色低透明」），避免飞过浅色纸面时出现一圈脏黑边。in-place 返回 img。"""
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            lum = max(r, g, b)
            if lum <= 10:
                img.setPixel(x, y, 0x00000000)
                continue
            f = 255.0 / lum
            c.setRed(min(255, int(r * f)))
            c.setGreen(min(255, int(g * f)))
            c.setBlue(min(255, int(b * f)))
            c.setAlpha(min(255, int(lum * 1.5)))
            img.setPixelColor(x, y, c)
    return img


def _make_gold_dragon_pixmap(w, h):
    """生成一张 (w, h) 尺寸的金龙图：优先使用 assets/images/gift_show.png 素材
    （按比例缩放 + 抠掉黑底，结果按尺寸缓存）；素材缺失时回退到离屏手绘的金色小龙。"""
    src = _load_dragon_source()
    if src is not None and not src.isNull():
        key = (w, h)
        cached = _dragon_pixmap_cache.get(key)
        if cached is not None:
            return cached
        px = src.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        img = px.toImage().convertToFormat(QImage.Format_ARGB32)
        result = QPixmap.fromImage(_key_out_black(img))
        _dragon_pixmap_cache[key] = result
        return result
    # ---- 兜底：离屏手绘金色小龙 ----
    pad = max(4, int(min(w, h) * 0.16))
    s = min(w, h) + pad * 2
    img = QImage(s, s, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    glow = QRadialGradient(s / 2.0, s / 2.0, s / 2.0)
    glow.setColorAt(0.0, QColor(255, 205, 70, 200))
    glow.setColorAt(1.0, QColor(255, 200, 60, 0))
    p.setBrush(QBrush(glow))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, s, s)
    f = QFont()
    f.setPixelSize(int(min(w, h) * 0.95))
    p.setFont(f)
    p.drawText(img.rect(), Qt.AlignCenter, random.choice(("🐉", "🐲")))
    p.end()
    gold = QImage(s, s, QImage.Format_ARGB32)
    gold.fill(QColor(255, 196, 40))
    p2 = QPainter(img)
    p2.setCompositionMode(QPainter.CompositionMode_Multiply)
    p2.drawImage(0, 0, gold)
    p2.end()
    return QPixmap.fromImage(img)


def _make_glow_pixmap(w, h):
    """生成一张 (w,h) 的金色径向辉光（中心亮、向外渐隐），作为金龙背后的光晕。"""
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    cx, cy = w / 2.0, h / 2.0
    g = QRadialGradient(cx, cy, max(cx, cy))
    g.setColorAt(0.0, QColor(255, 240, 170, 255))
    g.setColorAt(0.22, QColor(255, 205, 90, 220))
    g.setColorAt(0.45, QColor(255, 160, 45, 130))
    g.setColorAt(0.7, QColor(255, 130, 30, 55))
    g.setColorAt(1.0, QColor(255, 110, 20, 0))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, w, h)
    p.end()
    return QPixmap.fromImage(img)


class _DragonWidget(QWidget):
    """自绘金龙 + 背后辉光：用 `scale`(整体缩放弹跳)、`glow_alpha`(光晕明暗呼吸) 与
    `glow_scale`(光晕大小脉动) 三个属性交给动画驱动（QWidget 没有 setTransform，
    缩放用 QPainter 在 paintEvent 里做）。"""

    def __init__(self, glow_px, dragon_px, caption="", parent=None):
        super().__init__(parent)
        self._glow = glow_px
        self._dragon = dragon_px
        self._caption = caption or ""
        self._scale = 1.0
        self._glow_alpha = 0.85
        self._glow_scale = 1.0

    def _get_scale(self):
        return self._scale

    def _set_scale(self, v):
        self._scale = v
        self.update()

    scale = pyqtProperty(float, _get_scale, _set_scale)

    def _get_glow_alpha(self):
        return self._glow_alpha

    def _set_glow_alpha(self, v):
        self._glow_alpha = v
        self.update()

    glow_alpha = pyqtProperty(float, _get_glow_alpha, _set_glow_alpha)

    def _get_glow_scale(self):
        return self._glow_scale

    def _set_glow_scale(self, v):
        self._glow_scale = v
        self.update()

    glow_scale = pyqtProperty(float, _get_glow_scale, _set_glow_scale)

    def paintEvent(self, event):
        if self._glow is None or self._dragon is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        # 背后辉光：整体缩放 * 光晕脉动缩放
        p.save()
        p.translate(cx, cy)
        p.scale(self._scale * self._glow_scale, self._scale * self._glow_scale)
        p.translate(-cx, -cy)
        p.setOpacity(self._glow_alpha)
        gw, gh = self._glow.width(), self._glow.height()
        p.drawPixmap(int((w - gw) / 2), int((h - gh) / 2), self._glow)
        p.restore()
        # 龙本体：仅整体缩放
        p.save()
        p.translate(cx, cy)
        p.scale(self._scale, self._scale)
        p.translate(-cx, -cy)
        p.setOpacity(1.0)
        dw, dh = self._dragon.width(), self._dragon.height()
        p.drawPixmap(int((w - dw) / 2), int((h - dh) / 2), self._dragon)
        # 龙上方的「感谢 xxx 送的 xx」文案（随龙一起缩放弹跳）
        if self._caption:
            self._draw_caption(p, w, h, dh)
        p.restore()
        p.end()

    def _draw_caption(self, p, w, h, dh):
        """在龙正上方绘制金色描边文案（感谢 xxx 送的 xx），随龙一起缩放弹跳。"""
        # 文案放在龙顶之上、与龙之间留一点间距；widget 顶部留出安全边距
        dragon_top = (h - dh) / 2.0
        top = max(8.0, dragon_top - 14.0)
        # 字号随控件宽度自适应，过长则缩小直到能放下；仍放不下则省略号截断
        fs = max(13, min(26, int(w * 0.072)))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(fs)
        fm = QFontMetrics(font)
        while fm.width(self._caption) > w - 16 and fs > 12:
            fs -= 1
            font.setPixelSize(fs)
            fm = QFontMetrics(font)
        text = self._caption
        if fm.width(text) > w - 16:
            text = fm.elidedText(text, Qt.ElideRight, w - 16)
        p.setFont(font)
        rect = QRectF(0, top, w, max(20.0, fm.height()))
        # 暗色投影：先偏移画一层深色，再画金色填充，保证浅纸/深底上都清晰
        p.setPen(QColor(40, 24, 0, 210))
        p.drawText(rect.adjusted(1.5, 1.5, 1.5, 1.5), Qt.AlignCenter, text)
        p.setPen(QColor(255, 222, 110))
        p.drawText(rect, Qt.AlignCenter, text)


def spawn_golden_dragon(parent, nickname="", gift_name=""):
    """在 parent 控件「底部 1/3 处」居中固定显示一条金龙：
    入场缩放弹跳 + 淡入 -> 停留（背后金色光晕呼吸）-> 淡出，结束后自动销毁。
    龙上方显示「感谢 <昵称> 送的 <礼物>」文案。
    parent 不可见或尺寸为 0 时直接跳过。"""
    if parent is None or not parent.isVisible() or parent.width() <= 0 or parent.height() <= 0:
        return
    w, h = parent.width(), parent.height()
    # 龙上方文案：感谢 xxx 送的 xx（缺省给个兜底称呼）
    nick = (nickname or "").strip() or "神秘贵人"
    gift = (gift_name or "").strip() or "礼物"
    caption = "感谢 {} 送的 {}".format(nick, gift)
    # 显示宽度：作为停留主视觉明显放大（窗口越宽越大，最小 180）
    disp_w = max(180, min(w, h) // 2)

    # 按素材原始宽高比算出显示高度（dragon_gold.png 约 1.5:1）；无素材时退化为方形
    src = _load_dragon_source()
    if src is not None and not src.isNull() and src.height() > 0:
        disp_h = max(30, int(disp_w * src.height() / src.width()))
    else:
        disp_h = disp_w

    # 光晕比龙更大（尤其上下方向），widget 要足够大以免裁切光晕
    gw = int(disp_w * 2.3)
    gh = int(disp_h * 3.2)
    dragon_px = _make_gold_dragon_pixmap(disp_w, disp_h)
    glow_px = _make_glow_pixmap(gw, gh)

    wrap = _DragonWidget(glow_px, dragon_px, caption, parent)
    wrap.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    # 手机预览窗等父级用了无选择器 setStyleSheet("background:...")，会级联到子孙控件；
    # 这里显式声明透明背景，避免金龙带着一块父级底色出现在纸面上。
    wrap.setStyleSheet("background: transparent;")
    wrap.setFixedSize(gw, gh)
    wrap.raise_()

    # 固定在底部 1/3 区域、水平居中；多条金龙叠加时加一点随机偏移避免完全重叠
    band_top = h * 2 // 3                       # 底部 1/3 的起点
    base_x = (w - gw) // 2
    base_y = int(band_top + (h - band_top - gh) / 2)
    jitter = max(0, int(min(w, h) * 0.05))
    x = base_x + random.randint(-jitter, jitter)
    y = base_y + random.randint(-jitter, jitter)
    wrap.move(x, y)
    wrap.show()

    opacity = QGraphicsOpacityEffect(wrap)
    opacity.setOpacity(0.0)
    wrap.setGraphicsEffect(opacity)

    fade = 600
    hold = random.randint(2500, 4200)           # 停留几秒再淡出

    # 入场：缩放弹跳(0.55->1.0, OutBack 过冲) + 整体淡入
    scale_in = QPropertyAnimation(wrap, b"scale")
    scale_in.setDuration(fade)
    scale_in.setStartValue(0.55)
    scale_in.setEndValue(1.0)
    scale_in.setEasingCurve(QEasingCurve.OutBack)

    fade_in = QPropertyAnimation(opacity, b"opacity")
    fade_in.setDuration(fade)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_in.setEasingCurve(QEasingCurve.InOutQuad)

    # 退场：淡出 + 轻微缩小
    fade_out = QPropertyAnimation(opacity, b"opacity")
    fade_out.setDuration(fade)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.setEasingCurve(QEasingCurve.InOutQuad)

    scale_out = QPropertyAnimation(wrap, b"scale")
    scale_out.setDuration(fade)
    scale_out.setStartValue(1.0)
    scale_out.setEndValue(0.85)
    scale_out.setEasingCurve(QEasingCurve.InOutQuad)

    enter = QParallelAnimationGroup(wrap)
    enter.addAnimation(scale_in)
    enter.addAnimation(fade_in)

    exit_grp = QParallelAnimationGroup()
    exit_grp.addAnimation(fade_out)
    exit_grp.addAnimation(scale_out)

    seq = QSequentialAnimationGroup(wrap)
    seq.addAnimation(enter)
    seq.addPause(hold)
    seq.addAnimation(exit_grp)

    # 光晕呼吸（停留期间独立循环，给龙加「光效动效」）：明暗 + 大小双脉动
    glow_up = QPropertyAnimation(wrap, b"glow_alpha")
    glow_up.setDuration(900)
    glow_up.setStartValue(0.6)
    glow_up.setEndValue(1.0)
    glow_up.setEasingCurve(QEasingCurve.InOutSine)
    glow_down = QPropertyAnimation(wrap, b"glow_alpha")
    glow_down.setDuration(900)
    glow_down.setStartValue(1.0)
    glow_down.setEndValue(0.6)
    glow_down.setEasingCurve(QEasingCurve.InOutSine)
    glow_alpha_breathe = QSequentialAnimationGroup()
    glow_alpha_breathe.addAnimation(glow_up)
    glow_alpha_breathe.addAnimation(glow_down)
    glow_alpha_breathe.setLoopCount(max(1, (hold - fade) // 1800 + 1))

    glow_scale_up = QPropertyAnimation(wrap, b"glow_scale")
    glow_scale_up.setDuration(1100)
    glow_scale_up.setStartValue(0.92)
    glow_scale_up.setEndValue(1.1)
    glow_scale_up.setEasingCurve(QEasingCurve.InOutSine)
    glow_scale_down = QPropertyAnimation(wrap, b"glow_scale")
    glow_scale_down.setDuration(1100)
    glow_scale_down.setStartValue(1.1)
    glow_scale_down.setEndValue(0.92)
    glow_scale_down.setEasingCurve(QEasingCurve.InOutSine)
    glow_scale_breathe = QSequentialAnimationGroup()
    glow_scale_breathe.addAnimation(glow_scale_up)
    glow_scale_breathe.addAnimation(glow_scale_down)
    glow_scale_breathe.setLoopCount(max(1, (hold - fade) // 2200 + 1))

    def _cleanup():
        glow_alpha_breathe.stop()
        glow_scale_breathe.stop()
        wrap.deleteLater()
    seq.finished.connect(_cleanup)

    # 入场后再开启呼吸，避免与淡入抢同一属性
    QTimer.singleShot(fade, glow_alpha_breathe.start)
    QTimer.singleShot(fade, glow_scale_breathe.start)
    seq.start()


# ---------------------------------------------------------------------------
# 自绘控件：红章 / 评分章 / 头像圆章
# ---------------------------------------------------------------------------

class _Stamp(QWidget):
    """旋转红框章（来了/赞/谢谢/粉丝/弹幕/玩法名…）。"""

    def __init__(self, text, size=14, parent=None):
        super().__init__(parent)
        self._text = text
        self._size = size
        self._rot = -14
        fm = QFontMetrics(_mono_font(size))
        w = fm.width(text) + 16
        h = fm.height() + 8
        self._w, self._h = max(w, 30), max(h, 22)
        self.setFixedSize(self._w, self._h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self._w / 2, self._h / 2)
        p.rotate(self._rot)
        pen = QPen(QColor(STAMP_RED), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = QRectF(-self._w / 2 + 2, -self._h / 2 + 2, self._w - 4, self._h - 4)
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(STAMP_RED))
        p.setFont(_mono_font(self._size, bold=True))
        p.drawText(r, Qt.AlignCenter, self._text)


class _ScoreStamp(QWidget):
    """旋转红框评分章（80~99 分）。"""

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self._value = value
        self._rot = -14
        self.setFixedSize(48, 30)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.translate(self.width() / 2, self.height() / 2)
        p.rotate(self._rot)
        pen = QPen(QColor(STAMP_RED), 2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = QRectF(-self.width() / 2 + 2, -self.height() / 2 + 2,
                   self.width() - 4, self.height() - 4)
        p.drawRoundedRect(r, 6, 6)
        p.setPen(QColor(STAMP_RED))
        p.setFont(_mono_font(14, bold=True))
        p.drawText(QRectF(-self.width() / 2, -self.height() / 2,
                          self.width() - 14, self.height()),
                   Qt.AlignCenter, "%d" % self._value)
        p.setFont(_mono_font(8))
        p.drawText(QRectF(self.width() / 2 - 16, self.height() / 2 - 14,
                          16, 14), Qt.AlignCenter, "分")


class _AvatarBadge(QWidget):
    """圆形头像：带 URL 时显示图，否则昵称首字色块（色相由昵称哈希稳定）。"""

    def __init__(self, char, hue, size=60, parent=None):
        super().__init__(parent)
        self._char = char
        self._hue = hue
        self._size = size
        self._pix = None
        self.setFixedSize(size, size)

    def set_pixmap(self, pix):
        self._pix = pix
        self.update()

    def paintEvent(self, event):
        d = self._size
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addEllipse(1, 1, d - 2, d - 2)
        p.setClipPath(path)
        if self._pix and not self._pix.isNull():
            scaled = self._pix.scaled(d - 2, d - 2,
                                      Qt.KeepAspectRatioByExpanding,
                                      Qt.SmoothTransformation)
            p.drawPixmap(1, 1, scaled)
        else:
            p.fillRect(0, 0, d, d,
                       QColor.fromHsl(int(self._hue / 360.0 * 255), 140, 130))
            p.setPen(Qt.white)
            p.setFont(_mono_font(max(18, d // 3), bold=True))
            p.drawText(QRectF(0, 0, d, d), Qt.AlignCenter, self._char)
        p.setPen(QPen(QColor(INK), 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(1, 1, d - 2, d - 2)


class _AvatarWithScore(QWidget):
    """头像 + 压在右下缘的评分红章。"""

    def __init__(self, char, hue, score, parent=None):
        super().__init__(parent)
        self.setFixedSize(78, 72)
        self._badge = _AvatarBadge(char, hue, 60)
        self._badge.setParent(self)
        self._badge.move(6, 6)
        self._score = _ScoreStamp(score)
        self._score.setParent(self)
        self._score.move(46, 40)

    def set_pixmap(self, pix):
        self._badge.set_pixmap(pix)


# ---------------------------------------------------------------------------
# 单张小票（仿 receipt.html 的 .ticket）
# ---------------------------------------------------------------------------

class _CompactTicket(QWidget):
    """单行紧凑票：进场/关注/点赞/粉丝/普通弹幕。"""

    def __init__(self, text, stamp, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:%s; border-bottom:1px dashed #d8d2c4;" % PAPER_BG)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 18)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        label.setFont(_mono_font(13))
        label.setStyleSheet("color:%s; background:transparent;" % INK)
        lay.addWidget(label)
        self._stamp = _Stamp(stamp, 13)
        self._stamp.setParent(self)

    def resizeEvent(self, event):
        self._stamp.move(self.width() - self._stamp.width() - 6,
                         self.height() - self._stamp.height() - 4)
        super().resizeEvent(event)


class _MultiTicket(QWidget):
    """多行票：礼物 / 命中隐藏玩法。"""

    def __init__(self, header, meta, lines, foot, stamp, nick, hue, score,
                 avatar_url, parent=None):
        super().__init__(parent)
        self._avatar_url = avatar_url
        self.setStyleSheet("background:%s; border-bottom:1px dashed #d8d2c4;" % PAPER_BG)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 14)
        lay.setSpacing(4)

        h = QLabel(header)
        h.setAlignment(Qt.AlignCenter)
        h.setFont(_mono_font(13, bold=True))
        h.setStyleSheet("color:%s; font-weight:bold; background:transparent;" % INK)
        m = QLabel(meta)
        m.setAlignment(Qt.AlignCenter)
        m.setFont(_mono_font(10))
        m.setStyleSheet("color:%s; background:transparent;" % META)

        self._avatar = _AvatarWithScore(nick[:1] or "?", hue, score)
        center = QHBoxLayout()
        center.setContentsMargins(0, 0, 0, 0)
        center.addStretch(1)
        center.addWidget(self._avatar)
        center.addStretch(1)

        lay.addWidget(h)
        lay.addWidget(m)
        lay.addLayout(center)

        for bold, txt in lines:
            l = QLabel(txt)
            l.setAlignment(Qt.AlignCenter)
            l.setWordWrap(True)
            l.setFont(_mono_font(13, bold=bold))
            l.setStyleSheet("color:%s; background:transparent;" % INK)
            lay.addWidget(l)

        sep = QLabel("-" * 16)
        sep.setAlignment(Qt.AlignCenter)
        sep.setFont(_mono_font(12))
        sep.setStyleSheet("color:%s; background:transparent; letter-spacing:2px;" % SEP)
        lay.addWidget(sep)

        for fl in foot.split("\n"):
            if not fl:
                continue
            f = QLabel(fl)
            f.setAlignment(Qt.AlignCenter)
            f.setFont(_mono_font(11))
            f.setStyleSheet("color:%s; background:transparent;" % FOOT)
            lay.addWidget(f)

        self._stamp = _Stamp(stamp, 14)
        self._stamp.setParent(self)

    def resizeEvent(self, event):
        self._stamp.move(self.width() - self._stamp.width() - 8,
                         self.height() - self._stamp.height() - 34)
        super().resizeEvent(event)

    def set_avatar_pix(self, pix):
        self._avatar.set_pixmap(pix)


# ---------------------------------------------------------------------------
# 消息 → 小票内容（文案对齐 receipt.html 的 STYLES）
# ---------------------------------------------------------------------------

_TICKET_SEQ = 0


def _compact(text, stamp):
    return {"kind": "compact", "text": text, "stamp": stamp}


def _multi(header, meta, lines, foot, stamp, nick, hue, avatar_url):
    return {"kind": "multi", "header": header, "meta": meta, "lines": lines,
            "foot": foot, "stamp": stamp, "nick": nick, "hue": hue,
            "avatar_url": avatar_url, "score": random.randint(80, 99)}


def _build_ticket(msg_obj):
    """按 receipt.html 的样式，把一条直播消息映射成小票内容 dict。"""
    global _TICKET_SEQ
    src = str(msg_obj.get("source", "") or "").strip()
    nick = str(msg_obj.get("nickName", "") or "").strip()
    if not nick:
        return None

    now = datetime.datetime.now()
    _TICKET_SEQ += 1
    meta = "No.%03d  %02d:%02d" % (_TICKET_SEQ % 1000, now.hour, now.minute)
    hue = sum(ord(c) for c in nick) % 360

    if src == "进场":
        return _compact("欢迎 %s 进入直播间" % nick, "来了")
    if src == "关注":
        return _compact("感谢 %s 关注了主播，么么哒" % nick, "已关注")
    if src == "点赞":
        cnt = msg_obj.get("goodCount", 1) or 1
        return _compact("%s 点亮了小心心 x%s" % (nick, cnt), "赞")
    if src == "粉丝":
        txt = str(msg_obj.get("msg", "") or "")
        body = "%s 加入粉丝团%s" % (nick, (" " + txt) if txt else "")
        return _compact(body, "粉丝")
    if src == "礼物":
        gift = str(msg_obj.get("giftName", "") or "礼物")
        cnt = msg_obj.get("giftCount", 1) or 1
        # 注意：礼物价值(price)仅用于底层逻辑判断（高价值优先/头像阈值等），
        # 绝不渲染到任何页面 UI，避免用户直观看到金额。
        header = "==== %s*跪谢老板 ====" % PRINTER_HEADER
        lines = [("b", "感谢 %s 的支持！" % nick),
                 ("", "收到：%s x%d" % (gift, cnt))]
        foot = "您的支持是我们最大的动力\n%s · 谢谢惠顾" % PRINTER_HEADER
        avatar_url = str(msg_obj.get("avatar", "") or "")
        return _multi(header, meta, lines, foot, "谢谢", nick, hue, avatar_url)
    if src == "聊天":
        msg = str(msg_obj.get("msg", "") or "").strip()
        if not msg:
            return None
        style = None
        key = msg_obj.get("chat_style_key")
        if key:
            style = next((s for s in CHAT_STYLES if s.get("key") == key), None)
        if style is None:
            style = classify_chat_style(msg)
        if style:
            name = style.get("name", "")
            title = style.get("title", name) or name
            header = "==== %s*%s ====" % (PRINTER_HEADER, title)
            lines = [("b", "感谢 %s 触发隐藏玩法！" % nick),
                     ("", "玩法：%s" % name),
                     ("", "留言：%s" % msg)]
            for d in (style.get("decoration") or [])[:2]:
                lines.append(("", d))
            foot = style.get("footer") or "隐藏玩法已解锁，666"
            return _multi(header, meta, lines, foot, name, nick, hue, "")
        return _compact("%s：%s" % (nick, msg), "弹幕")
    return None


def _build_card_widget(ticket):
    """把小票内容 dict 渲染成可见控件（compact / multi 两种）。

    PC 模拟页与手机预览窗共用，保证两套界面视觉、数据完全一致。
    """
    if ticket["kind"] == "compact":
        return _CompactTicket(ticket["text"], ticket["stamp"])
    return _MultiTicket(ticket["header"], ticket["meta"], ticket["lines"],
                        ticket["foot"], ticket["stamp"], ticket["nick"],
                        ticket["hue"], ticket["score"], ticket["avatar_url"])


# ---------------------------------------------------------------------------
# 头像异步下载线程（复用 printer_manager 的磁盘缓存下载器）
# ---------------------------------------------------------------------------

class _AvatarWorker(QThread):
    """后台下载头像，完成后发信号回主线程刷新对应小票。"""

    loaded = pyqtSignal(str, str)          # (url, 本地文件路径)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self._url = url

    def run(self):
        try:
            path = download_avatar(self._url)
        except Exception as e:             # download_avatar 内部已兜底，双保险
            logging.warning("[小票模拟] 头像下载异常: %s", e)
            path = None
        if path:
            self.loaded.emit(self._url, path)


class _AvatarBus:
    """按 URL 去重的头像下载调度器（同一 URL 只下载一次，全局共享）。"""

    def __init__(self, on_ready):
        self._on_ready = on_ready          # 回调：ready(url, pixmap)
        self._pixmaps = {}                 # url -> QPixmap
        self._pending = set()              # 正在下载的 url
        self._workers = []

    def pixmap_for(self, url, size):
        """有缓存返回 QPixmap（调用方自己缩放）；没有则触发后台下载，返回 None。"""
        if not url:
            return None
        pix = self._pixmaps.get(url)
        if pix is not None:
            return pix
        if url not in self._pending:
            self._pending.add(url)
            worker = _AvatarWorker(url)
            worker.loaded.connect(self._on_loaded)
            worker.finished.connect(lambda w=worker: self._cleanup(w))
            self._workers.append(worker)
            worker.start()
        return None

    def _on_loaded(self, url, path):
        pix = QPixmap(path)
        if not pix.isNull():
            self._pixmaps[url] = pix
            self._on_ready(url)

    def _cleanup(self, worker):
        try:
            self._workers.remove(worker)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# 小票模拟打印页（出纸口 + 暖白纸卷 + 工具按钮）
# ---------------------------------------------------------------------------

class ReceiptSimulator(QWidget):
    """「小票模拟打印」整页：出纸口 + 暖白纸卷（最新在最上方）+ 工具按钮。"""

    # 跨线程补偿事件（由后台 idle 线程 emit，主线程槽接住后 add_event）
    idle_fill_requested = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards = []                   # 保留的小票（最新在 index 0）
        self._avatar_bus = _AvatarBus(self._on_avatar_ready)
        self._avatar_cards = {}            # url -> [card, ...] 等待回填的小票
        self.phone_win = None              # 手机预览独立窗口（懒加载，复用不重建）
        self.printer3d_win = None          # 3D打印现场独立窗口（懒加载，复用不重建）
        self._event_backlog = []           # 本轮直播已生成的票（dict），用于开手机预览时回放，保证与 PC 一致
        self._printer3d_backlog = []       # 最近若干条真实事件原文，用于开 3D打印 时回放
        self._init_ui()

        # 空闲补偿发送：参考 live_server 的「空闲随机补发」，独立到 utils/idle_filler。
        # 一分钟内（idle_seconds）没收到真实数据且任一预览窗口可见时，补发一条互动小票，
        # 经 add_event 同时喂给 PC 小票模拟与手机预览（手机预览复用同一张票）。
        self.idle_fill_requested.connect(self._on_idle_fill)
        self._filler = IdleFiller(
            on_fill=self._request_idle_fill,
            is_active=self._filler_active,
            idle_seconds_range=(30, 60),
        )
        self._filler.start()

    # ---------- UI ----------
    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部：打印机出纸口（深色机身 + 横向开口）
        printer_mouth = QWidget()
        printer_mouth.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #3b3454, stop:1 #221d39);")
        mouth_lay = QVBoxLayout(printer_mouth)
        mouth_lay.setContentsMargins(14, 8, 14, 6)
        mouth_lay.setSpacing(4)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        brand = QLabel("树洞 · 小票打印机")
        brand.setStyleSheet("color:rgba(255,255,255,0.72); font-size:13px; "
                            "letter-spacing:2px; background:transparent;")
        bar.addStretch()
        bar.addWidget(brand)
        bar.addStretch()
        mouth_lay.addLayout(bar)

        slot = QWidget()
        slot.setFixedHeight(10)
        slot.setStyleSheet(
            "background:linear-gradient(180deg,#000,#0c0917); border-radius:8px;")
        mouth_lay.addWidget(slot)

        lip = QWidget()                    # 压纸沿：纸从它下面钻出来
        lip.setFixedHeight(5)
        lip.setStyleSheet(
            "background:linear-gradient(180deg,#2a2440,#1a1630);"
            "border-radius:0 0 8px 8px;")
        mouth_lay.addWidget(lip)

        foot = QHBoxLayout()               # 中：致谢文案（居中）  右：年月日时分秒实时时钟
        foot.setContentsMargins(0, 0, 0, 0)
        foot_style = ("color:rgba(255,255,255,0.42); font-size:11px; "
                      "letter-spacing:2px; background:transparent;")
        marquee = QLabel("感谢姥爷们分享直播间")
        marquee.setStyleSheet(foot_style)
        self._clock = _make_clock_label(foot_style)
        foot.addStretch()
        foot.addWidget(marquee)
        foot.addStretch()
        foot.addWidget(self._clock)
        mouth_lay.addLayout(foot)

        outer.addWidget(printer_mouth)

        # 暖白纸卷
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setStyleSheet(
            "QScrollArea{background:%s;}"
            "QScrollBar:vertical{background:transparent; width:8px;}"
            "QScrollBar::handle:vertical{background:#cfc8b8; border-radius:4px;"
            " min-height:30px;}"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            "{height:0px;}" % PAPER_BG)

        self.strip = QWidget()
        self.strip.setStyleSheet("background:%s;" % PAPER_BG)
        self.strip_lay = QVBoxLayout(self.strip)
        self.strip_lay.setAlignment(Qt.AlignTop)
        self.strip_lay.setSpacing(0)
        self.strip_lay.setContentsMargins(10, 12, 10, 24)

        tip = QLabel("— 小票会随直播消息自动打出 —")
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet("color:#b9b09c; font-size:12px; background:transparent;")
        self.tip_label = tip
        self.strip_lay.addWidget(tip)

        self.scroll.setWidget(self.strip)
        outer.addWidget(self.scroll)

        # 底部操作栏：模拟一条 / 清空 / 手机预览（右对齐，延续机身深色）
        bottom = QWidget()
        bottom.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #2a2440, stop:1 #1a1630);")
        blay = QHBoxLayout(bottom)
        blay.setContentsMargins(14, 5, 14, 7)
        blay.addStretch()
        self.btn_demo = QPushButton("模拟一条")
        self.btn_demo.setCursor(Qt.PointingHandCursor)
        self.btn_demo.setToolTip("注入一条演示小票（进场/礼物/聊天/点赞/关注随机）")
        self.btn_demo.clicked.connect(self.on_demo)
        self.btn_demo.setStyleSheet(_btn_style("#4a5160", "#566072"))
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_clear.setStyleSheet(_btn_style("#4a5160", "#566072"))
        self.btn_phone = QPushButton("手机预览")
        self.btn_phone.setCursor(Qt.PointingHandCursor)
        self.btn_phone.setToolTip("弹出手机尺寸的预览窗；内部小票与「小票模拟」完全一致（同套渲染/红章/头像/数据），打开即回放当前已打出的小票")
        self.btn_phone.clicked.connect(self.open_phone_window)
        self.btn_phone.setStyleSheet(_btn_style("#2f7d4f", "#3a9a61"))
        blay.addWidget(self.btn_demo)
        blay.addWidget(self.btn_clear)
        blay.addWidget(self.btn_phone)
        outer.addWidget(bottom)

    # ---------- 公共：追加一条真实直播消息 ----------
    def add_event(self, msg_obj, synthetic=False):
        """msg_obj: 结构化消息 dict（source/nickName/msg/giftName/price/avatar…）。

        在主线程调用（liveMain.append_message 已保证）。
        synthetic=True 表示空闲补偿的演示事件：只进小票/手机预览，不进 3D打印现场，
        保证「3D打印」画面里出现的都是真实抓到的事件。
        """
        # 真实数据到达 -> 刷新空闲补偿计时（一分钟内无真实数据才会触发补偿）
        self._filler.mark_activity()

        # 3D打印现场：与小票/手机预览共用同一条真实事件（补偿事件不转发）
        if not synthetic:
            try:
                self._printer3d_backlog.append(dict(msg_obj))
                if len(self._printer3d_backlog) > 8:
                    self._printer3d_backlog.pop(0)
                win = self.printer3d_win
                if win is not None and win.isVisible():
                    win.add_event(msg_obj)
            except Exception as e:
                logging.warning("[小票模拟] 3D打印现场转发失败: %s", e)

        try:
            ticket = _build_ticket(msg_obj)
        except Exception as e:
            logging.warning("[小票模拟] 生成小票失败: %s", e)
            return
        if ticket is None:
            return
        # 回放队列存「已生成的票」：点开手机预览时连同票号一起灌入，
        # 保证手机窗与 PC 小票在编号、内容上逐项对应，绝不打折
        self._event_backlog.append(ticket)
        if len(self._event_backlog) > MAX_CARDS:
            self._event_backlog.pop(0)
        try:
            card = _build_card_widget(ticket)
        except Exception as e:
            logging.warning("[小票模拟] 生成小票控件失败: %s", e)
            return
        self._append_card(card, ticket)

        # 实时转发给已打开的手机预览窗口（同一张票，UI/数据完全一致）
        if self.phone_win is not None and self.phone_win.isVisible():
            try:
                self.phone_win.add_event(ticket)
            except Exception as e:
                logging.warning("[小票模拟] 手机预览转发失败: %s", e)

        # 礼物：按概率随机飞出一条金龙（PC 与手机预览同步，活跃气氛）
        if msg_obj.get("source") == "礼物" and random.random() < GIFT_DRAGON_PROB:
            _nick = msg_obj.get("nickName", "") or ""
            _gift = msg_obj.get("giftName", "") or ""
            self._spawn_dragon(_nick, _gift)
            if self.phone_win is not None and self.phone_win.isVisible():
                self.phone_win._spawn_dragon(_nick, _gift)
            play_dragon_roar()

    def _spawn_dragon(self, nickname="", gift_name=""):
        """礼物触发时在自身上方飞出一条金龙（视觉点缀，详见 spawn_golden_dragon）。"""
        spawn_golden_dragon(self, nickname, gift_name)

    # ---------- 空闲补偿发送（utils/idle_filler） ----------
    def _filler_active(self):
        """是否应该补发：小票模拟页或手机预览任一可见时为真。"""
        if self.isVisible():
            return True
        if self.phone_win is not None and self.phone_win.isVisible():
            return True
        return False

    def _request_idle_fill(self):
        """由 IdleFiller 后台线程调用：生成补偿事件并通过信号切回主线程。"""
        self.idle_fill_requested.emit(random_interaction_event())

    @pyqtSlot(object)
    def _on_idle_fill(self, evt):
        """主线程接收补偿事件，走与真实消息完全相同的渲染/转发路径。"""
        try:
            self.add_event(evt, synthetic=True)
        except Exception as e:
            logging.warning("[小票模拟] 空闲补偿出票失败: %s", e)

    # ---------- 纸带追加/滚动/上限 ----------
    def _append_card(self, card, ticket):
        self.tip_label.hide()
        self.strip_lay.insertWidget(0, card)   # 最新贴在出纸口（最上方）
        self._cards.insert(0, card)
        _fade_in(card)

        # 礼物头像：预览时异步下载，回填时再画（避免阻塞出纸）
        avatar_url = ticket.get("avatar_url", "")
        if avatar_url:
            pix = self._avatar_bus.pixmap_for(avatar_url, 60)
            if pix is not None:
                card.set_avatar_pix(pix)
            else:
                self._avatar_cards.setdefault(avatar_url, []).append(card)

        while len(self._cards) > MAX_CARDS:
            old = self._cards.pop()           # 最旧在末尾
            self.strip_lay.removeWidget(old)
            old.deleteLater()

        # 用户在看历史（离顶部较远）时不强行拉回；否则保持最新在出纸口可见
        bar = self.scroll.verticalScrollBar()
        if bar.value() <= 30:
            bar.setValue(0)

    # ---------- 头像回填 ----------
    def _on_avatar_ready(self, url):
        cards = self._avatar_cards.pop(url, [])
        pix = self._avatar_bus._pixmaps.get(url)
        if pix is None:
            return
        for card in cards:
            try:
                card.set_avatar_pix(pix)
            except RuntimeError:
                pass                        # 小票已被丢弃（deleteLater 后）

    # ---------- 按钮 ----------
    def on_clear(self):
        for card in self._cards:
            self.strip_lay.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self.tip_label.show()

    def on_demo(self):
        """注入一条演示小票（走与真实消息完全相同的渲染路径）。"""
        self.add_event(_make_demo_event())

    # ---------- 手机预览窗口 ----------
    def open_phone_window(self):
        """弹出（或唤起）手机尺寸的预览窗。

        内部小票与「小票模拟」(PC) 完全一致：复用同一张已生成的票（含票号），
        所以 UI、数据逐项对应。打开时一次性回放本轮流水，打开即是当前内容。
        """
        if self.phone_win is None:
            self.phone_win = PhoneReceiptWindow()
            # 仅首次创建时回放，避免重复点击造成小票翻倍
            for m in self._event_backlog:
                try:
                    self.phone_win.add_event(m)
                except Exception as e:
                    logging.warning("[小票模拟] 手机预览回放失败: %s", e)
        self.phone_win.show()
        self.phone_win.raise_()
        self.phone_win.activateWindow()

    # ---------- 3D打印现场窗口 ----------
    def open_printer3d_window(self):
        """弹出（或唤起）3D打印现场：龙门架逐层打印头像牌 + 传送带运走成品。

        首次打开时回放最近几条真实事件，打开即有画面；之后由 add_event() 实时
        转发同一条事件（礼物插队）。空闲补位事件不进本窗口。
        """
        if self.printer3d_win is None:
            self.printer3d_win = Printer3DWindow()
            try:
                self.printer3d_win.seed(self._printer3d_backlog)
            except Exception as e:
                logging.warning("[小票模拟] 3D打印现场回放失败: %s", e)
        self.printer3d_win.show()
        self.printer3d_win.raise_()
        self.printer3d_win.activateWindow()

    # ---------- 关闭兜底 ----------
    def closeEvent(self, event):
        try:
            self._filler.stop()
        except Exception:
            pass
        if self.phone_win is not None:
            try:
                self.phone_win.close()
            except Exception:
                pass
            self.phone_win = None
        if self.printer3d_win is not None:
            try:
                self.printer3d_win.close()
            except Exception:
                pass
            self.printer3d_win = None
        for w in self._avatar_bus._workers:
            w.quit()
            if not w.wait(500):
                w.terminate()
        super().closeEvent(event)


def _btn_style(bg, hover):
    return ("QPushButton{padding:4px 12px; font-size:12px; color:#e8ecf3;"
            "background:%s; border:1px solid %s; border-radius:4px;}"
            "QPushButton:hover{background:%s;}" % (bg, hover, hover))


# ---------------------------------------------------------------------------
# 手机预览独立窗口（手机尺寸外壳，内部小票与 PC「小票模拟」完全一致）
# ---------------------------------------------------------------------------

class PhoneReceiptWindow(QWidget):
    """点击「手机预览」按钮弹出的独立窗口：手机尺寸外壳，内部小票与「小票模拟」
    （PC）完全一致——同一套 _build_ticket 渲染、同样的红章/评分章/头像、数据同步。

    仅外壳是手机尺寸，方便在电脑上预览手机端吐纸效果；收到的就是 PC 已经生成好
    的那张票（含票号），因此两边编号、内容逐项对应。
    """

    PHONE_W, PHONE_H = 390, 844

    def __init__(self, parent=None):
        super().__init__(None, Qt.Window)
        self._cards = []                   # 保留的小票（最新在 index 0）
        self._avatar_bus = _AvatarBus(self._on_avatar_ready)
        self._avatar_cards = {}            # url -> [card, ...] 等待回填的小票
        self.setWindowTitle("树洞 · 小票打印机")
        self.setFixedSize(self.PHONE_W, self.PHONE_H)
        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("background:#0e0f12;")
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        screen = QFrame()
        screen.setStyleSheet("QFrame{background:%s; border-radius:14px;}" % PAPER_BG)
        sroot = QVBoxLayout(screen)
        sroot.setContentsMargins(0, 0, 0, 0)
        sroot.setSpacing(0)

        # 顶部机身（仿 receipt.html 外侧，紧凑版）：品牌 + LED / 出纸口 / 压纸沿 / 文案
        machine = QWidget()
        machine.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            "stop:0 #3b3454, stop:1 #221d39);"
            "border-top-left-radius:14px; border-top-right-radius:14px;")
        mlay = QVBoxLayout(machine)
        mlay.setContentsMargins(10, 5, 10, 3)
        mlay.setSpacing(2)

        mbar = QHBoxLayout()
        mbar.setContentsMargins(0, 0, 0, 0)
        mbrand = QLabel("树洞 · 小票打印机")
        mbrand.setStyleSheet("color:rgba(255,255,255,0.72); font-size:11px; "
                             "letter-spacing:2px; background:transparent;")
        mbar.addStretch()
        mbar.addWidget(mbrand)
        mbar.addStretch()
        mlay.addLayout(mbar)

        mslot = QWidget()
        mslot.setFixedHeight(8)
        mslot.setStyleSheet(
            "background:linear-gradient(180deg,#000,#0c0917); border-radius:6px;")
        mlay.addWidget(mslot)

        mlip = QWidget()
        mlip.setFixedHeight(4)
        mlip.setStyleSheet("background:#1a1630; border-radius:0 0 6px 6px;")
        mlay.addWidget(mlip)

        mfoot = QHBoxLayout()              # 左：致谢文案  右：年月日时分秒实时时钟
        mfoot.setContentsMargins(0, 0, 0, 0)
        mfoot_style = ("color:rgba(255,255,255,0.42); font-size:10px; "
                       "letter-spacing:1px; background:transparent;")
        mmarquee = QLabel("感谢姥爷们分享直播间")
        mmarquee.setStyleSheet(mfoot_style)
        mfoot.addWidget(mmarquee)
        mfoot.addStretch()
        mfoot.addWidget(_make_clock_label(mfoot_style))
        mlay.addLayout(mfoot)

        sroot.addWidget(machine)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)   # 隐藏右侧滚动条
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea{background:%s;}" % PAPER_BG)
        self.strip = QWidget()
        self.strip.setStyleSheet("background:%s;" % PAPER_BG)
        self.strip_lay = QVBoxLayout(self.strip)
        self.strip_lay.setContentsMargins(8, 8, 8, 16)
        self.strip_lay.setSpacing(0)
        self.strip_lay.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.strip)
        sroot.addWidget(self.scroll, 1)

        bar = QWidget()
        bar.setStyleSheet(
            "background:#1f2128; border-bottom-left-radius:14px; "
            "border-bottom-right-radius:14px;")
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(10, 4, 10, 5)
        self.btn_demo = QPushButton("模拟一条")
        self.btn_demo.setCursor(Qt.PointingHandCursor)

        def _on_demo():
            # 手机窗「模拟一条」自己生成的事件也要触发金龙（PC 侧只负责转发，
            # 不会替手机窗触发），礼物按概率出龙 + 咆哮音效
            evt = _make_demo_event()
            t = _build_ticket(evt)
            if t:
                self.add_event(t)
                if evt.get("source") == "礼物" and random.random() < GIFT_DRAGON_PROB:
                    self._spawn_dragon(evt.get("nickName", "") or "",
                                       evt.get("giftName", "") or "")
                    play_dragon_roar()
        self.btn_demo.clicked.connect(_on_demo)
        self.btn_demo.setStyleSheet(_phone_btn_style())
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.on_clear)
        self.btn_clear.setStyleSheet(_phone_btn_style())
        blay.addWidget(self.btn_demo)
        blay.addWidget(self.btn_clear)
        sroot.addWidget(bar)

        root.addWidget(screen)

    # ---------- 外部：追加一张已生成好的票（与 PC 同一张，编号/内容一致） ----------
    def add_event(self, ticket):
        if not isinstance(ticket, dict) or ticket.get("kind") not in ("compact", "multi"):
            return
        try:
            card = _build_card_widget(ticket)
        except Exception as e:
            logging.warning("[手机预览] 生成小票控件失败: %s", e)
            return
        self._append_card(card, ticket)

    def _spawn_dragon(self, nickname="", gift_name=""):
        """礼物触发时在自身上方飞出一条金龙（视觉点缀，详见 spawn_golden_dragon）。"""
        spawn_golden_dragon(self, nickname, gift_name)

    # ---------- 纸带追加/滚动/上限（与 PC 模拟页一致：最新在最上方） ----------
    def _append_card(self, card, ticket):
        self.strip_lay.insertWidget(0, card)   # 最新贴在出纸口（最上方）
        self._cards.insert(0, card)
        _fade_in(card)

        # 头像异步下载回填（与 PC 一致）
        avatar_url = ticket.get("avatar_url", "")
        if avatar_url:
            pix = self._avatar_bus.pixmap_for(avatar_url, 60)
            if pix is not None:
                card.set_avatar_pix(pix)
            else:
                self._avatar_cards.setdefault(avatar_url, []).append(card)

        while len(self._cards) > MAX_CARDS:
            old = self._cards.pop()           # 最旧在末尾
            self.strip_lay.removeWidget(old)
            old.deleteLater()

        # 最新在出纸口（顶部），用户没在翻看历史时保持贴顶可见
        bar = self.scroll.verticalScrollBar()
        if bar.value() <= 30:
            bar.setValue(0)

    # ---------- 头像回填 ----------
    def _on_avatar_ready(self, url):
        cards = self._avatar_cards.pop(url, [])
        pix = self._avatar_bus._pixmaps.get(url)
        if pix is None:
            return
        for card in cards:
            try:
                card.set_avatar_pix(pix)
            except RuntimeError:
                pass                        # 小票已被丢弃（deleteLater 后）

    def on_clear(self):
        for card in self._cards:
            self.strip_lay.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._avatar_cards.clear()

    # ---------- 关闭清理头像下载线程 ----------
    def closeEvent(self, event):
        for w in self._avatar_bus._workers:
            w.quit()
            if not w.wait(500):
                w.terminate()
        super().closeEvent(event)


def _phone_btn_style():
    return ("QPushButton{padding:5px 14px; font-size:12px; color:#e8ecf3;"
            "background:#3a4150; border:1px solid #4a5160; border-radius:6px;}"
            "QPushButton:hover{background:#4a5160;}")
