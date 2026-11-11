# app_state.py
import threading

class AppState:
    _lock = threading.Lock()
    # 消息记录：默认仅勾选「礼物」，其余默认不勾
    show_gift = True
    show_msg = False
    show_like = False
    show_follow = False
    show_enter = False
    show_stats = False
    show_fans = False

    # 语音播报：默认全部不勾
    show_gift_voice = False
    show_msg_voice = False
    show_like_voice = False
    show_follow_voice = False
    show_enter_voice = False

    # 打印类勾选状态（按消息类型独立控制 USB 小票打印）
    show_chat_print = False
    show_gift_print = False
    show_like_print = False
    show_enter_print = False
    show_follow_print = False
    show_fans_print = False

    # 直播平台：默认仅勾选「抖音」
    chk_ks = False
    chk_dy = True
    
    live_title = "直播互动控制器"
    live_version = "1.0.9"


    @classmethod
    def set_show_gift(cls, value: bool):
        with cls._lock:
            cls.show_gift = value
    @classmethod
    def get_show_gift(cls) -> bool:
        with cls._lock:
            return cls.show_gift
        
    @classmethod
    def set_show_msg(cls, value: bool):
        with cls._lock:
            cls.show_msg = value
    @classmethod
    def get_show_msg(cls) -> bool:
        with cls._lock:
            return cls.show_msg

    @classmethod
    def set_show_like(cls, value: bool):
        with cls._lock:
            cls.show_like = value
    @classmethod
    def get_show_like(cls) -> bool:
        with cls._lock:
            return cls.show_like

    @classmethod
    def set_show_follow(cls, value: bool):
        with cls._lock:
            cls.show_follow = value
    @classmethod
    def get_show_follow(cls) -> bool:
        with cls._lock:
            return cls.show_follow

    @classmethod
    def set_show_enter(cls, value: bool):
        with cls._lock:
            cls.show_enter = value
    @classmethod
    def get_show_enter(cls) -> bool:
        with cls._lock:
            return cls.show_enter

    @classmethod
    def set_show_stats(cls, value: bool):
        with cls._lock:
            cls.show_stats = value
    @classmethod
    def get_show_stats(cls) -> bool:
        with cls._lock:
            return cls.show_stats

    @classmethod
    def set_show_fans(cls, value: bool):
        with cls._lock:
            cls.show_fans = value
    @classmethod
    def get_show_fans(cls) -> bool:
        with cls._lock:
            return cls.show_fans
        
    @classmethod
    def set_show_gift_voice(cls, value: bool):
        with cls._lock:
            cls.show_gift_voice = value
    @classmethod
    def get_show_gift_voice(cls) -> bool:
        with cls._lock:
            return cls.show_gift_voice
    @classmethod
    def set_show_msg_voice(cls, value: bool):
        with cls._lock:
            cls.show_msg_voice = value
    @classmethod
    def get_show_msg_voice(cls) -> bool:
        with cls._lock:
            return cls.show_msg_voice
    @classmethod
    def set_show_like_voice(cls, value: bool):
        with cls._lock:
            cls.show_like_voice = value
    @classmethod
    def get_show_like_voice(cls) -> bool:
        with cls._lock:
            return cls.show_like_voice
    @classmethod
    def set_show_follow_voice(cls, value: bool):
        with cls._lock:
            cls.show_follow_voice = value
    @classmethod
    def get_show_follow_voice(cls) -> bool:
        with cls._lock:
            return cls.show_follow_voice
    @classmethod
    def set_show_enter_voice(cls, value: bool):
        with cls._lock:
            cls.show_enter_voice = value
    @classmethod
    def get_show_enter_voice(cls) -> bool:
        with cls._lock:
            return cls.show_enter_voice
    # ===== 打印类（按消息类型） =====
    @classmethod
    def set_show_chat_print(cls, value: bool):
        with cls._lock:
            cls.show_chat_print = value
    @classmethod
    def get_show_chat_print(cls) -> bool:
        with cls._lock:
            return cls.show_chat_print

    @classmethod
    def set_show_gift_print(cls, value: bool):
        with cls._lock:
            cls.show_gift_print = value
    @classmethod
    def get_show_gift_print(cls) -> bool:
        with cls._lock:
            return cls.show_gift_print

    @classmethod
    def set_show_like_print(cls, value: bool):
        with cls._lock:
            cls.show_like_print = value
    @classmethod
    def get_show_like_print(cls) -> bool:
        with cls._lock:
            return cls.show_like_print

    @classmethod
    def set_show_enter_print(cls, value: bool):
        with cls._lock:
            cls.show_enter_print = value
    @classmethod
    def get_show_enter_print(cls) -> bool:
        with cls._lock:
            return cls.show_enter_print

    @classmethod
    def set_show_follow_print(cls, value: bool):
        with cls._lock:
            cls.show_follow_print = value
    @classmethod
    def get_show_follow_print(cls) -> bool:
        with cls._lock:
            return cls.show_follow_print

    @classmethod
    def set_show_fans_print(cls, value: bool):
        with cls._lock:
            cls.show_fans_print = value
    @classmethod
    def get_show_fans_print(cls) -> bool:
        with cls._lock:
            return cls.show_fans_print

    # ===== 高价值礼物优先（语音插队 + 打印插队） =====
    # 礼物价值（钻，= diamond_count × 数量）达到此值即视为「大礼物」
    high_value_threshold = 500
    high_value_priority_voice = True   # 大礼物是否优先语音播报（插队到队列最前，不打断当前播放）
    high_value_priority_print = True   # 大礼物是否优先打印（插队 + 强制头像 + 大礼物样式）

    @classmethod
    def set_high_value_threshold(cls, value: int):
        with cls._lock:
            try:
                cls.high_value_threshold = int(value)
            except (TypeError, ValueError):
                pass
    @classmethod
    def get_high_value_threshold(cls) -> int:
        with cls._lock:
            return cls.high_value_threshold

    @classmethod
    def set_high_value_priority_voice(cls, value: bool):
        with cls._lock:
            cls.high_value_priority_voice = value
    @classmethod
    def get_high_value_priority_voice(cls) -> bool:
        with cls._lock:
            return cls.high_value_priority_voice

    @classmethod
    def set_high_value_priority_print(cls, value: bool):
        with cls._lock:
            cls.high_value_priority_print = value
    @classmethod
    def get_high_value_priority_print(cls) -> bool:
        with cls._lock:
            return cls.high_value_priority_print

    @classmethod
    def set_chk_ks(cls, value: bool):
        with cls._lock:
            cls.chk_ks = value
    @classmethod
    def get_chk_ks(cls) -> bool:
        with cls._lock:
            return cls.chk_ks
    @classmethod
    def set_chk_dy(cls, value: bool):
        with cls._lock:
            cls.chk_dy = value
    @classmethod
    def get_chk_dy(cls) -> bool:
        with cls._lock:
            return cls.chk_dy
    @classmethod
    def get_live_version(cls) -> str:
        with cls._lock:
            return cls.live_version

    # ===== 激活状态（激活功能已移除，程序默认可用，无需激活） =====
    activated = True           # 是否已激活（已固定为 True，无需激活即可使用）
    act_code = ""              # 激活码（保留字段，不再使用）
    expire = None              # 过期时间（保留字段，不再使用）

    @classmethod
    def set_activated(cls, value: bool):
        # 激活功能已移除：保留接口以避免外部调用报错，但不再改变可用状态
        with cls._lock:
            cls.activated = True
    @classmethod
    def get_activated(cls) -> bool:
        # 激活功能已移除：恒为 True，程序无需激活即可使用
        with cls._lock:
            return True

    @classmethod
    def set_act_code(cls, value: str):
        with cls._lock:
            cls.act_code = value or ""
    @classmethod
    def get_act_code(cls) -> str:
        with cls._lock:
            return cls.act_code

    @classmethod
    def set_expire(cls, value):
        with cls._lock:
            cls.expire = value
    @classmethod
    def get_expire(cls):
        with cls._lock:
            return cls.expire
