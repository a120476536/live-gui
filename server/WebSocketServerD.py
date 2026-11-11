#!/usr/bin/python
# coding:utf-8

# @FileName:    WebSocketServerD.py
# @Time:        2024/1/2 21:51
# @Author:      bubu
# @Project:     douyinLiveWebFetcher
# @test : 66186758468
import sys,json,inspect
import codecs
import gzip
import hashlib
import random
import re
import string
import subprocess
import threading
import time
import urllib.parse
from contextlib import contextmanager
from unittest.mock import patch

import requests 
import websocket 
from py_mini_racer import MiniRacer # type: ignore

from protobuf.douyin import *
import os
from PyQt5.QtCore import QObject, pyqtSignal
from utils.SerialManager import serial_manager
import logging
from core.app_state import AppState
from core.tts_manager import TTSManager
from utils.ac_signature import get__ac_signature
from urllib3.util.url import parse_url
from requests.cookies import cookiejar_from_dict
# get_user_message 获取 cookie 
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# 获取当前程序目录
if getattr(sys, 'frozen', False):
    # 打包后的可执行文件运行
    base_dir = os.path.dirname(sys.executable)
else:
    # 开发环境中运行
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
def cfg_path():
    """返回 lock_cfg.json 的绝对路径（兼容开发/打包）"""
    lock_cfg_path = os.path.join(base_dir, "data")
    os.makedirs(lock_cfg_path, exist_ok=True)
    lock_cfg_file = os.path.join(lock_cfg_path, "lock_cfg.json")
    return lock_cfg_file


def _resource_path(rel_path: str) -> str:
    """兼容开发/打包的资源路径解析。

    打包（frozen）后 js / protobuf 等资源被 PyInstaller 放到 exe 同级的 ``_internal``
    目录；开发态则在项目根目录。data / log 仍留在 exe 同级，故这里只针对资源做兼容，
    不动 base_dir（避免影响 data 的解析）。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        internal = os.path.join(exe_dir, "_internal", rel_path)
        if os.path.exists(internal):
            return internal
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.exists(os.path.join(meipass, rel_path)):
            return os.path.join(meipass, rel_path)
    return os.path.join(base_dir, rel_path)

logging.info(f"这句路径 打包还是得带着 ：{os.path.join(os.path.dirname(inspect.getfile(MiniRacer)), 'mini_racer.dll')}")

def load_cfg():
    """第一次打开时读取 json"""
    try:
        with open(cfg_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"count": 0, "list": []}

@contextmanager
def patched_popen_encoding(encoding='utf-8'):
    original_popen_init = subprocess.Popen.__init__
    
    def new_popen_init(self, *args, **kwargs):
        kwargs['encoding'] = encoding
        original_popen_init(self, *args, **kwargs)
    
    with patch.object(subprocess.Popen, '__init__', new_popen_init):
        yield


def generateSignature(wss, script_file=None):
    """
    出现gbk编码问题则修改 python模块subprocess.py的源码中Popen类的__init__函数参数encoding值为 "utf-8"
    """
    logging.info(f"script_file first:={script_file}")
    if script_file is None:
        script_file = _resource_path('js/sign.js')
    logging.info(f"script_file second:={os.path.isfile(script_file)}")
    params = ("live_id,aid,version_code,webcast_sdk_version,"
              "room_id,sub_room_id,sub_channel_id,did_rule,"
              "user_unique_id,device_platform,device_type,ac,"
              "identity").split(',')
    wss_params = urllib.parse.urlparse(wss).query.split('&')
    wss_maps = {i.split('=')[0]: i.split("=")[-1] for i in wss_params}
    tpl_params = [f"{i}={wss_maps.get(i, '')}" for i in params]
    param = ','.join(tpl_params)
    md5 = hashlib.md5()
    md5.update(param.encode())
    md5_param = md5.hexdigest()
    
    with codecs.open(script_file, 'r', encoding='utf8') as f:
        script = f.read()
    
    ctx = MiniRacer()
    ctx.eval(script)
    
    try:
        signature = ctx.call("get_sign", md5_param)
        return signature
    except Exception as e:
        logging.info(f"{e}")
    
    # 以下代码对应js脚本为sign_v0.js
    # context = execjs.compile(script)
    # with patched_popen_encoding(encoding='utf-8'):
    #     ret = context.call('getSign', {'X-MS-STUB': md5_param})
    # return ret.get('X-Bogus')


def generateMsToken(length=182):
    """
    产生请求头部cookie中的msToken字段，其实为随机的182位字符
    :param length:字符位数
    :return:msToken
    """
    random_str = ''
    base_str = string.ascii_letters + string.digits + '-_'
    _len = len(base_str) - 1
    for _ in range(length):
        random_str += base_str[random.randint(0, _len)]
    return random_str


class DouyinLiveWebFetcher(QObject):
    dyDataReceiver = pyqtSignal(str)

    # 抖音推流里大量与礼物无关的「系统/UI」消息，确认无需处理，静默忽略（不再打日志）。
    # 后续若再发现确认无关的消息类型，往这里加即可；真正未知的 method 仍会按限流 INFO 报警，
    # 这样万一抖音把礼物 method 改名，能立刻在日志里看到。
    KNOWN_SYSTEM_METHODS = frozenset({
        "WebcastResidentGuestMessage",   # 常驻嘉宾
        "WebcastLowPcuGuideMessage",     # 低人气引导
        "WebcastInRoomBannerMessage",    # 直播间横幅
        "WebcastRoomCommentTopicMessage",# 评论话题
        "WebcastRoomDataSyncMessage",    # 房间数据同步
        "WebcastChatLikeMessage",        # 聊天+点赞合并
        "WebcastRoomIndicatorMessage",   # 房间指示器（如礼物面板状态等，非礼物消息本身）
    })

    def __init__(self, live_id, abogus_file=_resource_path('js/a_bogus.js'), cookie_string=''):
        super().__init__()
        """
        直播间弹幕抓取对象
        :param live_id: 直播间的直播id，打开直播间web首页的链接如：https://live.douyin.com/261378947940，
                        其中的261378947940即是live_id
        :param abogus_file: a_bogus.js 文件路径
        """
        self.abogus_file = abogus_file
        self.__ttwid = None
        self.__room_id = None
        self.session = requests.Session()
        self.live_id = live_id
        self.cookie_string = ""
        self.host = "https://www.douyin.com/"
        self.live_url = "https://live.douyin.com/"
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " \
                          "Chrome/140.0.0.0 Safari/537.36 Edg/140.0.0.0"
        self.headers = {
            'User-Agent': self.user_agent
        }
        self.set_cookie_string(cookie_string)
        # 未收录 method 的限流时间戳：避免每条都打日志刷屏，
        # 但保证每种 method 至少能看到一次（用于确认礼物等消息的真实 method 名称）
        self._unrecorded_log_ts = {}
        # 诊断用：记录已打印过「首次出现」的消息类型，保证每种 method 至少能看到一次，
        # 用于确认礼物等消息的真实 method 名称（改名后也能在此暴露）。
        self._seen_methods = set()

    def start(self):
        self._connectWebSocket()
    
    def stop(self):
        if hasattr(self, "ws") and self.ws:
            self.ws.close()

    # 抖音登录态 Cookie 的常见凭证字段（命中任意一条即可认为不是游客态）。
    # 2026-04 起抖音对 WebcastGiftMessage 做风控：游客 Cookie 能收聊天/点赞/进场，
    # 但服务端根本不下发礼物帧，客户端再怎么改解析都拿不到。
    LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss", "sid_tt", "sid_guard",
                         "uid_tt", "uid_tt_ss", "passport_auth_status")

    def set_cookie_string(self, cookie_string: str):
        self.cookie_string = (cookie_string or "").strip()
        if not self.cookie_string:
            return

        cookie_dict = {}
        for part in self.cookie_string.split(";"):
            item = part.strip()
            if not item or "=" not in item:
                continue
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()

        if cookie_dict:
            self.session.cookies.update(cookiejar_from_dict(cookie_dict))
            # 只打字段名、绝不打 Cookie 值，便于从日志一眼区分「没填 Cookie」
            # 与「填了但只是游客态 Cookie」。
            present = [k for k in self.LOGIN_COOKIE_KEYS if k in cookie_dict]
            if present:
                logging.info("抖音 Cookie 登录态自检：命中 %s（共 %d 字段）",
                             ",".join(present), len(cookie_dict))
            else:
                logging.warning(
                    "抖音 Cookie 为【游客态】：共 %d 字段，未含任何登录凭证（缺 %s）。"
                    "可正常收聊天/点赞/进场，但服务端不会下发 WebcastGiftMessage 礼物消息；"
                    "请在 www.douyin.com 登录后，从 /aweme/v1/web/ 请求复制完整 Cookie。",
                    len(cookie_dict), ",".join(self.LOGIN_COOKIE_KEYS))

    def _build_cookie_header(self, extra=None) -> str:
        cookie_items = []
        merged = requests.utils.dict_from_cookiejar(self.session.cookies)
        if extra:
            merged.update(extra)
        for key, value in merged.items():
            if value is None or value == "":
                continue
            cookie_items.append(f"{key}={value}")
        return "; ".join(cookie_items)
    
    @property
    def ttwid(self):
        """
        产生请求头部cookie中的ttwid字段，访问抖音网页版直播间首页可以获取到响应cookie中的ttwid
        :return: ttwid
        """
        if self.__ttwid:
            return self.__ttwid
        existing_ttwid = self.session.cookies.get("ttwid")
        if existing_ttwid:
            self.__ttwid = existing_ttwid
            return self.__ttwid
        headers = {
            "User-Agent": self.user_agent,
            "Cookie": self._build_cookie_header(),
        }
        try:
            response = self.session.get(self.live_url, headers=headers)
            response.raise_for_status()
        except Exception as err:
            logging.info("【X】Request the live url error: ", err)
        else:
            self.__ttwid = response.cookies.get('ttwid')
            return self.__ttwid
    
    @property
    def room_id(self):
        """
        根据直播间的地址获取到真正的直播间roomId，有时会有错误，可以重试请求解决
        :return:room_id
        """
        if self.__room_id:
            return self.__room_id
        url = self.live_url + self.live_id
        ms_token = self.session.cookies.get("msToken") or generateMsToken()
        self.session.cookies.set("msToken", ms_token)
        headers = {
            "User-Agent": self.user_agent,
            "Cookie": self._build_cookie_header({
                "ttwid": self.ttwid,
                "msToken": ms_token,
            }),
        }
        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
        except Exception as err:
            logging.info("【X】Request the live room url error: ", err)
        else:
            match = re.search(r'roomId\\":\\"(\d+)\\"', response.text)
            if match is None or len(match.groups()) < 1:
                logging.info("【X】No match found for roomId")
            
            self.__room_id = match.group(1)
            
            return self.__room_id
    
    def get_ac_nonce(self):
        """
        获取 __ac_nonce
        """
        headers = self.headers.copy()
        cookie_header = self._build_cookie_header()
        if cookie_header:
            headers["Cookie"] = cookie_header
        resp_cookies = self.session.get(self.host, headers=headers).cookies
        return resp_cookies.get("__ac_nonce")
    
    def get_ac_signature(self, __ac_nonce: str = None) -> str:
        """
        获取 __ac_signature
        """
        __ac_signature = get__ac_signature(self.host[8:], __ac_nonce, self.user_agent)
        self.session.cookies.set("__ac_signature", __ac_signature)
        return __ac_signature
    
    def get_a_bogus(self, url_params: dict):
        """
        获取 a_bogus
        """
        url = urllib.parse.urlencode(url_params)
        try:
            import execjs
            with open(self.abogus_file, 'r', encoding='utf-8') as file:
                js_code = file.read()
            ctx = execjs.compile(js_code)
            _a_bogus = ctx.call("get_ab", url, self.user_agent)
            return _a_bogus
        except Exception as e:
            logging.error(f"获取 a_bogus 失败: {e}")
            return ""
    
    def get_room_status(self):
        """
        获取直播间开播状态:
        room_status: 2 直播已经结束
        room_status: 0 直播进行中
        """
        try:
            msToken = self.session.cookies.get("msToken") or generateMsToken()
            self.session.cookies.set("msToken", msToken)
            nonce = self.get_ac_nonce()
            signature = self.get_ac_signature(nonce)
            url = ('https://live.douyin.com/webcast/room/web/enter/?aid=6383'
                   '&app_name=douyin_web&live_id=1&device_platform=web&language=zh-CN&enter_from=page_refresh'
                   '&cookie_enabled=true&screen_width=5120&screen_height=1440&browser_language=zh-CN&browser_platform=Win32'
                   '&browser_name=Edge&browser_version=140.0.0.0'
                   f'&web_rid={self.live_id}'
                   f'&room_id_str={self.room_id}'
                   '&enter_source=&is_need_double_stream=false&insert_task_id=&live_reason=&msToken=' + msToken)
            query = parse_url(url).query
            params = {i[0]: i[1] for i in [j.split('=') for j in query.split('&')]}
            a_bogus = self.get_a_bogus(params)  # 计算a_bogus,成功率不是100%，出现失败时重试即可
            url += f"&a_bogus={a_bogus}"
            headers = self.headers.copy()
            headers.update({
                'Referer': f'https://live.douyin.com/{self.live_id}',
                'Cookie': self._build_cookie_header({
                    'ttwid': self.ttwid,
                    '__ac_nonce': nonce,
                    '__ac_signature': signature,
                    'msToken': msToken,
                }),
            })
            resp = self.session.get(url, headers=headers)
            data = resp.json().get('data')
            if data:
                room_status = data.get('room_status')
                user = data.get('user')
                user_id = user.get('id_str')
                nickname = user.get('nickname')
                status_str = ['正在直播', '已经结束'][bool(room_status)]
                logging.info(f"【{nickname}】[{user_id}]直播间：{status_str}。")
                self.dyDataReceiver.emit(f"【{nickname}】[{user_id}]直播间：{status_str}。")
        except Exception as e:
            logging.error(f"获取直播间状态失败: {e}")
    
    def _connectWebSocket(self):
        """
        连接抖音直播间websocket服务器，请求直播间数据
        """
        wss = ("wss://webcast100-ws-web-lq.douyin.com/webcast/im/push/v2/?app_name=douyin_web"
               "&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
               "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web&cookie_enabled=true"
               "&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32"
               "&browser_name=Mozilla"
               "&browser_version=5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,"
               "%20like%20Gecko)%20Chrome/126.0.0.0%20Safari/537.36"
               "&browser_online=true&tz_name=Asia/Shanghai"
               "&cursor=d-1_u-1_fh-7392091211001140287_t-1721106114633_r-1"
               f"&internal_ext=internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:7319483754668557238"
               f"|first_req_ms:1721106114541|fetch_time:1721106114633|seq:1|wss_info:0-1721106114633-0-0|"
               f"wrds_v:7392094459690748497"
               f"&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3&endpoint=live_pc&support_wrds=1"
               f"&user_unique_id=7319483754668557238&im_path=/webcast/im/fetch/&identity=audience"
               f"&need_persist_msg_count=15&insert_task_id=&live_reason=&room_id={self.room_id}&heartbeatDuration=0")
        
        signature = generateSignature(wss)
        wss += f"&signature={signature}"
        
        headers = {
            "cookie": self._build_cookie_header({
                "ttwid": self.ttwid,
                "msToken": self.session.cookies.get("msToken") or generateMsToken(),
            }),
            'user-agent': self.user_agent,
        }
        self.ws = websocket.WebSocketApp(wss,
                                         header=headers,
                                         on_open=self._wsOnOpen,
                                         on_message=self._wsOnMessage,
                                         on_error=self._wsOnError,
                                         on_close=self._wsOnClose)
        try:
            self.ws.run_forever()
        except Exception:
            self.stop()
            raise
    
    def _sendHeartbeat(self):
        """
        发送心跳包
        """
        while True:
            try:
                heartbeat = PushFrame(payload_type='hb').SerializeToString()
                self.ws.send(heartbeat, websocket.ABNF.OPCODE_PING)
                logging.info("【✓】发送心跳包")
            except Exception as e:
                logging.info("【X】心跳包检测错误: %s", e)
                break
            else:
                time.sleep(5)
    
    def _wsOnOpen(self, ws):
        """
        连接建立成功
        """
        logging.info("【✓】WebSocket连接成功.")
        # daemon=True：心跳线程不能挡住进程退出（否则关 GUI 时进程会被它挂住）
        threading.Thread(target=self._sendHeartbeat, daemon=True).start()
    
    def _wsOnMessage(self, ws, message):
        """
        接收到数据
        :param ws: websocket实例
        :param message: 数据
        """
        try:
            # 根据proto结构体解析对象
            package = PushFrame().parse(message)
            response = Response().parse(gzip.decompress(package.payload))
            
            # 返回直播间服务器链接存活确认消息，便于持续获取数据
            if response.need_ack:
                ack = PushFrame(log_id=package.log_id,
                                payload_type='ack',
                                payload=response.internal_ext.encode('utf-8')
                                ).SerializeToString()
                ws.send(ack, websocket.ABNF.OPCODE_BINARY)
            
            # 根据消息类别解析消息体
            for msg in response.messages_list:
                method = msg.method
                # 诊断：每种消息类型首次出现时打印一次，便于确认礼物等消息的真实 method 名。
                if method not in self._seen_methods:
                    self._seen_methods.add(method)
                    logging.info("【WS方法】【首次】%s", method)
                try:
                    # 分派字典：method -> 对应解析方法。
                    # 注意：先 .get(method) 取出处理函数，未收录的 method 会得到 None，
                    # 必须判空后再调用，否则 None(msg.payload) 会抛
                    # 'NoneType' object is not callable（抖音推流里大量未知 method 会触发此错误）。
                    handler = {
                        'WebcastChatMessage': self._parseChatMsg,  # 聊天消息
                        'WebcastGiftMessage': self._parseGiftMsg,  # 礼物消息
                        'WebcastLikeMessage': self._parseLikeMsg,  # 点赞消息
                        'WebcastMemberMessage': self._parseMemberMsg,  # 进入直播间消息
                        'WebcastSocialMessage': self._parseSocialMsg,  # 关注消息
                        'WebcastRoomUserSeqMessage': self._parseRoomUserSeqMsg,  # 直播间统计
                        'WebcastFansclubMessage': self._parseFansclubMsg,  # 粉丝团消息
                        'WebcastControlMessage': self._parseControlMsg,  # 直播间状态消息
                        'WebcastEmojiChatMessage': self._parseEmojiChatMsg,  # 聊天表情包消息
                        'WebcastRoomStatsMessage': self._parseRoomStatsMsg,  # 直播间统计信息
                        'WebcastRoomMessage': self._parseRoomMsg,  # 直播间信息
                        'WebcastRoomRankMessage': self._parseRankMsg,  # 直播间排行榜信息
                        'WebcastRoomStreamAdaptationMessage': self._parseRoomStreamAdaptationMsg,  # 直播间流配置
                    }.get(method)
                    if handler is not None:
                        handler(msg.payload)
                    else:
                        # 未收录的消息类型：先判断是否为已知无害的「系统/UI」消息，是则静默忽略；
                        # 否则按限流 INFO 报警，便于确认礼物等消息是否因 method 更名而漏抓。
                        if method in self.KNOWN_SYSTEM_METHODS:
                            pass
                        else:
                            now = time.time()
                            last = self._unrecorded_log_ts.get(method, 0)
                            if now - last > 60:
                                self._unrecorded_log_ts[method] = now
                                logging.info("收到未收录消息类型 method=%s（礼物等若缺失请关注此日志）", method)
                except Exception as e:
                    logging.error(f"解析消息失败: {e}")
        except Exception as e:
            logging.error(f"解析数据失败{e}")
    
    def voice(self, data: dict):
        """处理语音播放"""
        if AppState.get_show_gift_voice():
            try:
                if data.get("source") == "礼物":
                    logging.info(f"礼物语音播报")
                    if data.get('nickName'):
                        price = data.get('price')
                        is_big = (isinstance(price, (int, float)) and
                                  AppState.get_high_value_priority_voice() and
                                  price >= AppState.get_high_value_threshold())
                        if is_big:
                            TTSManager.speak(
                                f"感谢{data['nickName']}送出的{data.get('giftName')}",
                                # f"价值{int(price)}钻，太感谢了！",
                                priority=True)
                        else:
                            TTSManager.speak(f"感谢{data['nickName']}送的{data.get('giftName')}")
            except Exception as e:
                logging.error(f"{data.get('source')} 播报语音失败{e}")
        
        # if AppState.get_show_msg_voice:
        #     if data.get("msg"):
        #         try:
        #             if data.get('nickName'):
        #                 TTSManager.speak(f"赞喜{data['nickName']}触发关键词，赠送一个笑脸")
        #             else:
        #                 TTSManager.speak(f"感谢 这个名字唸不出 送的{data.get('giftName')}触发关键词，赠送一个笑脸")
        #         except Exception as e:
        #             logging.error(f"消息 播报语音失败{e}")

        if AppState.get_show_like_voice():
            try:
                if data.get("source") == "点赞":
                    logging.info(f"点赞语音播报")
                    if data.get('nickName'):
                        TTSManager.speak(f"感谢{data['nickName']}的{data.get('source')}")
            except Exception as e:
                logging.error(f"{data.get('source')} 播报语音失败{e}")

        if AppState.get_show_follow_voice():
            try:
                if data.get("source") == "关注":
                    logging.info(f"关注语音播报")
                    if data.get('nickName'):
                        TTSManager.speak(f"感谢{data['nickName']}的{data.get('source')}")
            except Exception as e:
                logging.error(f"{data.get('source')} 播报语音失败{e}")

        if AppState.get_show_enter_voice():
            try:
                if data.get("source") == "进场":
                    logging.info(f"进场语音播报")
                    if data.get('nickName'):
                        TTSManager.speak(f"欢迎{data['nickName']}")
            except Exception as e:
                logging.error(f"{data.get('source')} 播报语音失败{e}")
    
    # def checkGift(self, gifName: str):
    #     """检查礼物是否在配置中"""
    #     lockDatas = load_cfg()  
    #     if isinstance(lockDatas, dict) and 'list' in lockDatas:
    #         for lockItem in lockDatas['list']:
    #             logging.info(f"输出所有配置的礼物数据：={lockItem}")
    #             if isinstance(lockItem, dict) and (lockItem.get('giftName') in gifName or lockItem.get('giftName') == gifName):
    #                 return lockItem
    #     return None
    def checkGift(self, gifName: str):
        """检查礼物是否在配置中"""
        lockDatas = load_cfg()  
        if isinstance(lockDatas, dict) and 'list' in lockDatas:
            for lockItem in lockDatas['list']:
                # logging.info(f"输出所有配置的礼物数据：={lockItem}")
                if isinstance(lockItem, dict):
                    config_gift_names = lockItem.get('giftName', '').split('|')
                    # 检查传入的礼物名称是否匹配配置中的任一礼物
                    if any(gift in gifName for gift in config_gift_names):
                        logging.info(f"检测赠送礼物在配置礼物列表输出={lockItem}")
                        return lockItem
        return None
    
    def getSetGifts(self):
        """获取配置的礼物列表"""
        lockDatas = load_cfg()  
        return lockDatas
    
    # 执行硬件处理
    def sendCmd(self, cmd: str):
        """发送硬件命令"""
        if serial_manager.is_open():
            serial_manager.send_command(cmd)
        else:
            logging.warning(f"串口未连接,请检查!")
    
    def action(self, data):
        """处理接收到的数据"""
        logging.info(f"action: 实际数据 {data.get('source')}-{data}")
        # logging.info(f"action: 实际获取数据后处理  礼物显示 {data.get('source') == '礼物'}")
        
        if data.get("source") == "礼物":
            self.dyDataReceiver.emit(json.dumps(data, ensure_ascii=False))
            # logging.info(f"action: 实际礼物名称 ：={data.get('giftName')}")
            # lockItem = self.checkGift(data.get("giftName"))
            # logging.info(f"action: 检查礼物 ：={lockItem}")
            if AppState.get_show_gift_voice():
                self.voice(data)
            # if lockItem:
            #     logging.info(f"action: 是否展示礼物：={AppState.get_show_gift()}-是否语音播报{AppState.get_show_gift_voice()}")
            #     if AppState.get_show_gift_voice():
            #         self.voice(data)
            #     if AppState.get_show_gift():
            #         self.dyDataReceiver.emit(f"{data}")
            #         logging.info(f"action: 实际获取数据后处理  检查礼物 存在 判断 显示礼物则 发送指令 ：={lockItem['cmd']}")
            #         self.sendCmd(lockItem['cmd'])
            # else:
            #     logging.info(f"action: 礼物配置不存在：={AppState.get_show_gift()}")
            #     if AppState.get_show_gift_voice():
            #         self.voice(data)
            #     if AppState.get_show_gift():
            #         self.dyDataReceiver.emit(f"{data}")
        
        if data.get("source") == "聊天":
            self.dyDataReceiver.emit(json.dumps(data, ensure_ascii=False))
            if AppState.get_show_msg_voice():
                self.voice(data)       
            
        if data.get("source") == "点赞":
            self.dyDataReceiver.emit(json.dumps(data, ensure_ascii=False))
            # logging.info("点赞") 
            # 检查点赞在礼物中的配置
            # lockItem = self.checkGift(data.get("source"))
            # logging.info(f"action: 实际获取点赞后 配置的礼物对象 ：={lockItem}")
            self.voice(data)
            # if lockItem:
            #     if AppState.get_show_like_voice():
            #         self.voice(data)
                    
            #     if AppState.get_show_like_voice():
            #         # self.dyDataReceiver.emit(f"{data}")
            #         logging.info(f"action: 点赞在礼物中配置有 发指令 ：={lockItem['cmd']}")
            #         self.sendCmd(lockItem['cmd'])
            
        if data.get("source") == "关注":
            self.dyDataReceiver.emit(json.dumps(data, ensure_ascii=False))
            # logging.info("关注")
            # lockItem = self.checkGift(data.get("source"))
            # logging.info(f"action: 实际获取关注后 配置的礼物对象 ：={lockItem}")
            self.voice(data)
            # if lockItem:
            #     if AppState.get_show_follow_voice():
            #         self.voice(data)
                    
            #     if AppState.get_show_follow_voice():
            #         # self.dyDataReceiver.emit(f"{data}")
            #         logging.info(f"action: 关注在礼物中配置有 发指令 ：={lockItem['cmd']}")
            #         self.sendCmd(lockItem['cmd'])

        if data.get("source") == "进场":
            # 用户进入直播间：把昵称 emit 出去，供 live_server 抓取后按参与数据格式推送（无需用户主动发言）
            self.dyDataReceiver.emit(json.dumps(data, ensure_ascii=False))
            # logging.info("进场")
            if AppState.get_show_enter_voice():
                self.voice(data)

        if data.get("source") == "排行榜":
            # 直播间人气榜单：只 emit 给 live_server（不播语音），小票页自行节流渲染
            self.dyDataReceiver.emit(json.dumps(data, ensure_ascii=False))
            
    
    def _wsOnError(self, ws, error):
        logging.info("WebSocket error: ", error)
    
    def _wsOnClose(self, ws, *args):
        self.get_room_status()
        logging.info("WebSocket connection closed.")
    
    def _parseChatMsg(self, payload):
        """聊天消息"""
        message = ChatMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        content = message.content
        # logging.info(f"【聊天msg】[{user_id}]{user_name}: {content}")
        data = {
            "from": "[抖音][观众]",
            "source": "聊天",
            "nickName": user_name,
            "msg": content,
            "avatar": self.get_avatar_url(message.user),   # 命中聊天玩法时按礼物样式出票，需要头像
        }
        self.action(data=data)
    
    def _parseGiftMsg(self, payload):
        """礼物消息"""
        message = GiftMessage().parse(payload)
        user = message.user
        gift = message.gift
        # 抖音近期字段结构有变动，部分礼物消息 user / gift 可能为 None，
        # 直接取 .nick_name / .name 会抛 AttributeError 被外层吞成「解析消息失败」，
        # 这里先判空并打礼物专属日志，便于区分「没收到礼物」还是「礼物结构解析失败」。
        if not user or not gift:
            logging.warning("礼物消息缺少 user 或 gift 字段，已跳过: user=%s gift=%s",
                            bool(user), bool(gift))
            return
        user_name = user.nick_name
        gift_name = gift.name
        gift_cnt = message.combo_count
        # 礼物单价（钻）来自 GiftStruct.diamond_count；
        # 抖音常只在付费礼物下发，免费礼物（如人气票）恒为 0
        gift_price = gift.diamond_count
        total_value = gift_price * gift_cnt if gift_price else 0
        avatar = self.get_avatar_url(user)
        logging.info(f"【礼物msg】{user_name} 送出了 {gift_name}x{gift_cnt} "
                      f"avatar:{avatar} 单价:{gift_price} 价值:{total_value}")
        data = {
            "from": "[抖音][观众]",
            "source": "礼物",
            "nickName": user_name,
            "giftName": gift_name,
            "giftCount": gift_cnt,
            "price": total_value,
            "avatar": avatar
        }
        self.action(data=data)

    def get_avatar_url(self, user) -> str:
        """获取用户头像URL"""

        if not user:
            return ""

        # 大 -> 中 -> 小
        for avatar in [
            user.avatar_large,
            user.avatar_medium,
            user.avatar_thumb
        ]:
            if avatar is None:
                continue

            url_list = avatar.url_list_list

            if not url_list:
                continue

            for url in url_list:
                if url:
                    return url

        return ""
    
    def _parseLikeMsg(self, payload):
        """点赞消息"""
        message = LikeMessage().parse(payload)
        user_name = message.user.nick_name
        count = message.count
        # logging.info(f"【点赞msg】{user_name} 点了{count}个赞")
        data = {
            "from": "[抖音][观众]",
            "source": "点赞",
            "nickName": user_name,
            "goodCount": count
        }
        self.action(data=data)
    
    def _parseMemberMsg(self, payload):
        """进入直播间消息"""
        message = MemberMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        # 抖音性别枚举原先只有 0=女 1=男，近期新增了「保密/未知」(值为 2 或更大)，
        # 直接用列表索引会 list index out of range 并导致该条进场消息解析失败、
        # 且每 5 秒周期性刷屏。改为安全映射，未知值一律记为「未知」。
        _gender_val = message.user.gender
        gender = {0: "女", 1: "男"}.get(_gender_val, "未知")
        # logging.info(f"【进场msg】[{user_id}][{gender}]{user_name} 进入了直播间")
        data = {
            "from": "[抖音][观众]",
            "source": "进场",
            "nickName": user_name,
        }
        self.action(data=data)
    
    def _parseSocialMsg(self, payload):
        """关注消息"""
        message = SocialMessage().parse(payload)
        user_name = message.user.nick_name
        user_id = message.user.id
        # logging.info(f"【关注msg】[{user_id}]{user_name} 关注了主播")
        data = {
            "from": "[抖音][观众]",
            "source": "关注",
            "nickName": user_name,
        }
        self.action(data=data)
    
    def _parseRoomUserSeqMsg(self, payload):
        """直播间统计"""
        message = RoomUserSeqMessage().parse(payload)
        current = message.total
        total = message.total_pv_for_anchor
        logging.info(f"【统计msg】当前观看人数: {current}, 累计观看人数: {total}")
        data = {
            "from": "[抖音][观众]",
            "source": "统计",
            "current": current,
            "total": total,
        }
        self.action(data=data)
    
    def _parseFansclubMsg(self, payload):
        """粉丝团消息"""
        message = FansclubMessage().parse(payload)
        content = message.content
        logging.info(f"【粉丝团msg】 {content}")
        data = {
            "from": "[抖音][观众]",
            "source": "粉丝",
            "nickName": (message.user.nick_name if message.user else "") or "",
            "msg": content,
        }
        self.action(data=data)
    
    def _parseEmojiChatMsg(self, payload):
        """聊天表情包消息"""
        message = EmojiChatMessage().parse(payload)
        emoji_id = message.emoji_id
        user = message.user
        common = message.common
        default_content = message.default_content
        logging.info(f"【聊天表情包id】 {emoji_id},user：{user},common:{common},default_content:{default_content}")
    
    def _parseRoomMsg(self, payload):
        """直播间消息"""
        message = RoomMessage().parse(payload)
        common = message.common
        room_id = common.room_id
        logging.info(f"【直播间msg】直播间id:{room_id}")
    
    def _parseRoomStatsMsg(self, payload):
        """直播间统计信息"""
        message = RoomStatsMessage().parse(payload)
        display_long = message.display_long
        logging.info(f"【直播间统计msg】{display_long}")
    
    def _parseRankMsg(self, payload):
        """直播间排行榜消息"""
        message = RoomRankMessage().parse(payload)
        ranks_list = message.ranks_list
        # 组装榜单（头像+昵称+分数）推给 live_server，供小票打印机页（/live/receipt）展示/打印人气榜单。
        # 每项为 RoomRankMessageRoomRank：user(nick_name/头像) + score_str。
        ranks = []
        for rank in ranks_list:
            user = rank.user
            nick = (user.nick_name or "").strip() if user else ""
            if not nick:
                continue
            ranks.append({
                "nickName": nick,
                "avatar": self.get_avatar_url(user),   # 大->中->小 取第一张可用头像
                "score": rank.score_str or "",         # 榜单有时不带分数，允许为空
            })
        if not ranks:
            return
        data = {
            "from": "[抖音][系统]",
            "source": "排行榜",
            "ranks": ranks,
        }
        # self.action(data=data)
        threading.Timer(30.0,self.action,kwargs={"data":data}).start()
    
    def _parseControlMsg(self, payload):
        """直播间状态消息"""
        message = ControlMessage().parse(payload)
        
        if message.status == 3:
            logging.info("直播间已经结束")
            self.stop()

    def _parseRoomStreamAdaptationMsg(self, payload):
        """直播间流配置"""
        message = RoomStreamAdaptationMessage().parse(payload)
        adaptationType = message.adaptation_type
        logging.info(f'直播间adaptation: {adaptationType}')
