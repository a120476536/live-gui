# -*- coding: utf-8 -*-
"""聊天「玩法」分类（关键字 + 文字长度）——独立轻量模块。

为什么要单独一份：
    live_server.py 只需要「给一条弹幕归类到某个玩法」这一件事，
    而 printer_manager.py 是物理小票机模块（escpos/系统打印队列，模块级还会起一个打印线程），
    让推送进程 import 它没必要也有副作用风险。因此这里只放纯配置 + 纯函数，
    不 import 任何第三方库、没有任何模块级副作用。

配置与 printer_manager.CHAT_STYLES 保持一致（改动时两边同步即可）。
"""

# 总开关：False 则聊天不判玩法
CHAT_STYLE_ENABLED = True
# True=只有命中玩法才推/打；False=未命中也当普通聊天处理
CHAT_PRINT_ONLY_MATCHED = False

# 玩法配置
#   key       : 内部标识
#   name      : 玩法名（小票上显示）
#   title     : 小票抬头
#   keywords  : 命中任一关键字即算匹配（支持中/英）
#   min_len   : 文字最少字数（含）；max_len: 最多字数（含）
CHAT_STYLES = [
    {
        "key": "love",
        "name": "爱情宣言",
        "title": "爱情宣言",
        "keywords": ["爱", "喜欢", "心动", "嫁", "娶", "在一起", "宝宝", "老公", "老婆",
                     "男友", "女友", "表白", "爱你", "么么", "亲亲", "脱单", "官宣", "想你"],
        "min_len": 6, "max_len": 60,
    },
    {
        "key": "vent",
        "name": "情绪发泄",
        "title": "心情树洞",
        "keywords": ["烦", "累", "哭", "崩溃", "生气", "讨厌", "压力", "难受", "委屈",
                     "想死", "滚", "破防", "emo", "摆烂", "焦虑", "失眠", "抑郁"],
        "min_len": 4, "max_len": 80,
    },
    {
        "key": "bless",
        "name": "祝福许愿",
        "title": "祝福许愿",
        "keywords": ["祝", "保佑", "好运", "暴富", "脱单", "上岸", "顺利", "健康",
                     "发财", "心想事成", "锦鲤", "逢考必过"],
        "min_len": 4, "max_len": 60,
    },
    {
        "key": "roast",
        "name": "吐槽八卦",
        "title": "吃瓜现场",
        "keywords": ["哈哈", "笑死", "无语", "服了", "离谱", "绝了", "草", "卧槽",
                     "真的假的", "666", "神金", "离大谱"],
        "min_len": 3, "max_len": 50,
    },
    {
        "key": "secret",
        "name": "神秘树洞",
        "title": "神秘树洞",
        "keywords": ["秘密", "树洞", "心里话", "悄悄话", "偷偷", "不敢说", "其实我",
                     "告诉你一个秘密", "说个秘密", "憋不住", "藏", "我只告诉你", "说实话",
                     "不敢跟别人", "只有你知道", "心事", "暗恋"],
        "min_len": 5, "max_len": 80,
    },
]


def classify_chat_style(text):
    """根据关键字 + 文字长度，把一条聊天归类为某个『玩法』；无匹配返回 None。

    判定顺序即 CHAT_STYLES 的顺序，命中第一个即返回。
    """
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


if __name__ == "__main__":
    # 自测：python chat_play.py
    cases = ["我好喜欢你啊宝宝", "今天好累真的崩溃了", "祝我暴富",
             "主播今天吃了吗", "我有个秘密想对树洞说", "哈哈哈哈笑死我了"]
    for c in cases:
        st = classify_chat_style(c)
        print("%-16s -> %s" % (c, st["name"] if st else "(未命中)"))
