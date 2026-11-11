# DyHttpServer.py
import json,sys,os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from PyQt5.QtCore import QObject, pyqtSignal
from utils.SerialManager import serial_manager
import logging
from core.app_state import AppState

from core.tts_manager import TTSManager

logging.basicConfig(
    level=logging.INFO,  # 显示 INFO 及以上级别日志
    format='%(asctime)s [%(levelname)s] %(message)s'
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
    # if getattr(sys, 'frozen', False):
    #     # 打包后运行
    #     return os.path.join(os.path.dirname(sys.executable), "lock_cfg.json")
    # else:
    #     # 开发环境
    #     return os.path.join(os.path.dirname(__file__), "lock_cfg.json")
    lock_cfg_path = os.path.join(base_dir, "data")
    os.makedirs(lock_cfg_path, exist_ok=True)  # 如果不存在就创建
    lock_cfg_file = os.path.join(lock_cfg_path, "lock_cfg.json")
    return lock_cfg_file
def load_cfg():
        """第一次打开时读取 json"""
        try:
            with open(cfg_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return {"count": 0, "list": []}
      
class HttpHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理类"""
    server_instance = None  # 用于传递 PyQt 信号
    
                
    def _set_headers(self, code=200):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()

    
    def checkGift(self,gifName:str):
        lockDatas = load_cfg()  
        if isinstance(lockDatas, dict) and 'list' in lockDatas:
            for lockItem in lockDatas['list']:
                if isinstance(lockItem, dict) and (lockItem.get('giftName') in gifName or lockItem.get('giftName') == gifName) :
                    return lockItem
        return None
    def getSetGifts(self):
        lockDatas = load_cfg()  
        return lockDatas
    # 执行硬件处理
    def sendCmd(self,cmd:str):
        if serial_manager.is_open():
            serial_manager.send_command(cmd)
        else:
            logging.warning(f"串口未连接,请检查!")
    def emitSend(self,data:str):
        if HttpHandler.server_instance:
                HttpHandler.server_instance.data_received.emit(f"{data}")
    # 赠送礼物真是名称
    def getRealGifName(self,data:dict):
        if data.get("giftName"):
            giftName = data['giftName'].replace('送', '').replace('出', '').replace('了', '')
            return giftName
        else:
            return None
    def voice(self,data:dict):
        if AppState.get_show_gift_voice():
            try:
                if data['nickName']:
                    TTSManager.speak(f"感谢{data['nickName']}送的{self.getRealGifName(data)}")
                else:
                    TTSManager.speak(f"感谢 这个名字咋读 送的{self.getRealGifName(data)}")
            except Exception as e:
                    logging.error(f"礼物 播报语音失败{e}")
        if AppState.get_show_msg_voice():
            if data.get("msg"):
                if AppState.get_show_msg_voice():
                    if data['nickName']:
                        TTSManager.speak(f"恭喜{data['nickName']}触发关键词，赠送一个笑脸")
                    else:
                        TTSManager.speak(f"感谢 这个名字咋读 送的{data['giftName']}触发关键词，赠送一个笑脸")
                
                    
    def do_GET(self):
        """GET /live-config：返回 GUI 勾选状态快照，供 live_server.py 轮询同步。"""
        path = self.path.split("?")[0]
        try:
            if path == "/live-config":
                cfg = {
                    # 消息发送开关（live_server 按此过滤是否广播该题型事件）
                    "show_gift": AppState.get_show_gift(),
                    "show_msg": AppState.get_show_msg(),
                    "show_like": AppState.get_show_like(),
                    "show_follow": AppState.get_show_follow(),
                    "show_enter": AppState.get_show_enter(),
                    "show_fans": AppState.get_show_fans(),
                    # 语音播报开关（live_server 的 WebSocketServerD 播报前检查）
                    "show_gift_voice": AppState.get_show_gift_voice(),
                    "show_msg_voice": AppState.get_show_msg_voice(),
                    "show_like_voice": AppState.get_show_like_voice(),
                    "show_follow_voice": AppState.get_show_follow_voice(),
                    "show_enter_voice": AppState.get_show_enter_voice(),
                }
                self._set_headers()
                self.wfile.write(json.dumps({"status": "ok", "config": cfg}).encode("utf-8"))
            else:
                self._set_headers(404)
                self.wfile.write(json.dumps({"error": "not found"}).encode("utf-8"))
        except Exception as e:
            logging.error(f"GET {self.path} 处理失败: {e}")

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data)
            logging.info(f"Received data: {data}")
            # self.emitSend(f"{data}")
            allSetGifts = self.getSetGifts()
            if  allSetGifts["count"] == 0 or not allSetGifts["list"]:
                self.emitSend(f"请配置控制器礼物数据")
                return
            if AppState.get_chk_dy():
                logging.info(f"data['from']:= {data['from']}这个抖音先不要了")
                # if '抖音' in data['from']:
                #     logging.info(f"'抖音' in data['from']:= {'抖音' in data['from']}")
                #     if '礼物' == data.get('source'):
                #         logging.info(f"data['source'] == '礼物':= {data['source'] == '礼物'}")
                #         gifName = data['giftName'].replace("送出", "")
                #         logging.info(f"抖音礼物name:= {gifName}")
                #         self.voice(data)
                #         lockItem = self.checkGift(gifName)
                #         if lockItem:
                #             logging.info(f"抖音礼物指令:= {lockItem['cmd']}")
                #             logging.info(f"抖音礼物发送勾选状态{AppState.get_show_gift()}")
                #             # 发送信号到 GUI
                #             if AppState.get_show_gift():
                #                 self.emitSend(f"礼物---{data['from']}{data['nickName']}赠送：{self.getRealGifName(data)}---预备：砰……")    
                #                 #serial_manager.send_command(lockItem['cmd'])
                #                 self.sendCmd(lockItem['cmd'])
                #         else:
                #             logging.info(f"抖音礼物发送勾选状态{AppState.get_show_gift()}")
                #             if AppState.get_show_gift():
                #                 logging.warning(f"抖音礼物配置：{gifName}不存在，不执行")
                #                 self.emitSend(f"礼物---{data['from']}{data['nickName']}赠送：{self.getRealGifName(data)}---未设置礼物不执行砰……")   
                            
                #     if "聊天" == data.get('source'):
                #         if AppState.get_show_msg():
                #             self.emitSend(f"聊天---{data['from']}:{data.get('nickName')}:{data.get('msg')}") 
                #         self.voice(data)
                #         if '给我玩一次,看我6不6' in data.get("msg"):
                #             if AppState.get_show_msg():
                #                 # 发送信号到 GUI
                #                 self.emitSend(f"聊天---{data['from']}-观众-{data['msg']}：---触发预设关键字指令 预备：砰……")    
                #                 #serial_manager.send_command(lockItem['cmd']) 
                #                 self.sendCmd(lockItem['cmd'])
                if '抖音' in data['from']:
                    logging.info(f"'抖音' in data['from']:= {'抖音' in data['from']}")
                    if data['source'] == '礼物':
                        logging.info(f"data['source'] == '礼物':= {data['source'] == '礼物'}")
                        gifName = data['giftName'].replace("送出", "")
                        logging.info(f"抖音礼物name:= {gifName}")
                        
                        lockItem = self.checkGift(gifName)
                        if lockItem:
                            logging.info(f"抖音礼物指令:= {lockItem['cmd']}")
                            logging.info(f"抖音礼物发送勾选状态{AppState.get_show_gift()}")
                            # 发送信号到 GUI
                            if AppState.get_show_gift():
                                self.emitSend(f"{data['from']}{data['nickName']}赠送：{gifName}---预备：砰……")    
                                #serial_manager.send_command(lockItem['cmd'])
                                self.sendCmd(lockItem['cmd'])
                        else:
                            logging.info(f"抖音礼物发送勾选状态{AppState.get_show_gift()}")
                            if AppState.get_show_gift():
                                logging.warning(f"抖音礼物配置：{gifName}不存在，不执行")
                                self.emitSend(f"{data['from']}{data['nickName']}赠送：{gifName}---未设置礼物不执行砰……")    
                    if data['source'] == "聊天":
                        if '给我玩一次,看我6不6' in data["msg"]:
                            if AppState.get_show_msg():
                                # 发送信号到 GUI
                                self.emitSend(f"{data['from']}-观众-{data['msg']}：---预备：砰……")    
                                #serial_manager.send_command(lockItem['cmd']) 
                                self.sendCmd(lockItem['cmd']) 
                        else:
                            if AppState.get_show_msg():
                                self.emitSend(f"{data}") 
                                   
            if AppState.get_chk_ks():            
                if '快手' in data['from']:
                    logging.info(f"'快手' in data['from']:= {'快手' in data['from']}")
                    if '礼物' == data.get('source'):
                        logging.info(f"data['source'] == '礼物':= {data['source'] == '礼物'}")
                        gifName = data['giftName'].replace("送出", "")
                        logging.info(f"快手礼物name:= {gifName}")
                        self.voice(data)
                    if data['source'] == '礼物':
                        logging.info(f"data['source'] == '礼物':= {data['source'] == '礼物'}")
                        gifName = data['giftName'].replace("送出", "")
                        logging.info(f"快手礼物name:= {gifName}")
                        lockItem = self.checkGift(gifName)
                        if lockItem:
                            logging.info(f"快手礼物指令:= {lockItem['cmd']}")
                            logging.info(f"快手礼物发送勾选状态{AppState.get_show_gift()}")
                            # 发送信号到 GUI
                            if AppState.get_show_gift():
                                self.emitSend(f"礼物---{data['from']}{data['nickName']}赠送：{self.getRealGifName(data)}---预备：砰……")    
                                #serial_manager.send_command(lockItem['cmd'])
                                self.sendCmd(lockItem['cmd'])
                        else:
                            logging.info(f"快手礼物发送勾选状态{AppState.get_show_gift()}")
                            logging.warning(f"快手礼物配置：{self.getRealGifName(data)}不存在，不执行")
                            # 发送信号到 GUI
                            if AppState.get_show_gift():
                                self.emitSend(f"礼物---{data['from']}{data['nickName']}赠送：{self.getRealGifName(data)}---未设置礼物不执行砰……")    
                                #serial_manager.send_command(lockItem['cmd'])
                    if "聊天" == data.get('source'):
                        self.voice(data)
                        if AppState.get_show_msg():
                            if data.get("msg"):
                                self.emitSend(f"聊天---{data['from']}:{data.get('nickName')}:{data.get('msg')}") 
                        if data.get("msg"):
                            if '给我玩一次,看我6不6' in data.get("msg"):
                                if AppState.get_show_msg():
                                    # 发送信号到 GUI
                                    self.emitSend(f"聊天---{data['from']}-观众-{data['msg']}：---预备：砰……")    
                                    #serial_manager.send_command(lockItem['cmd']) 
                                    self.sendCmd(lockItem['cmd']) 
                            self.emitSend(f"{data['from']}{data['nickName']}赠送：{gifName}---预备：砰……")    
                            #serial_manager.send_command(lockItem['cmd'])
                            self.sendCmd(lockItem['cmd'])
                        else:
                            logging.info(f"快手礼物发送勾选状态{AppState.get_show_gift()}")
                            logging.warning(f"快手礼物配置：{gifName}不存在，不执行")
                            self.emitSend(f"{data['from']}{data['nickName']}赠送：{gifName}---未设置礼物不执行砰……")    
                    if data['source'] == "聊天":
                        if '给我玩一次,看我6不6' in data["msg"]:
                            if AppState.get_show_msg():
                                # 发送信号到 GUI
                                self.emitSend(f"{data['from']}-观众-{data['msg']}：---预备：砰……")    
                                #serial_manager.send_command(lockItem['cmd']) 
                                self.sendCmd(lockItem['cmd']) 
                        else:
                            if AppState.get_show_msg():
                                self.emitSend(f"{data}") 
      
        except json.JSONDecodeError:
            data = {"error": "Invalid JSON"}
        
        # # 发送信号到 GUI
        # if HttpHandler.server_instance:
        #     HttpHandler.server_instance.data_received.emit(data)
        
        self._set_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))


class DyHttpServer(QObject):
    """封装 HTTPServer 的 PyQt 对象"""
    data_received = pyqtSignal(str)

    def __init__(self, host="0.0.0.0", port=8766):
        super().__init__()
        self.host = host
        self.port = port
        self.httpd = None
        self._thread = None
        
    
    def start(self):
        if self._thread and self._thread.is_alive():
            logging.info("HTTP Server already running")
            return

        # 重新创建 HTTPServer 和 Thread
        self.httpd = HTTPServer((self.host, self.port), HttpHandler)
        HttpHandler.server_instance = self
        self._thread = threading.Thread(target=self._run_server, daemon=True)
        self._thread.start()
        logging.info(f"HTTP Server started at http://{self.host}:{self.port}")
        self.data_received.emit(f"HTTP Server started at http://{self.host}:{self.port}")
    def _run_server(self):
        try:
            self.httpd.serve_forever()
        except Exception as e:
            # 修复日志格式
            logging.info("HTTP Server stopped: %s", e)

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        logging.info("HTTP Server stopped")
        self.data_received.emit("HTTP Server stopped")
