# import asyncio
# import edge_tts # type: ignore
# import os
# import sys
# import time
# from threading import Thread, Lock
# from queue import Queue
# import pygame # type: ignore
# import logging
# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s [%(levelname)s] %(message)s"
# )
# # ---- 超时与队列保护参数 ----
# TTS_GEN_TIMEOUT = 10      # 单条 TTS 生成（联网）最长等待秒数，超时即放弃本条
# TTS_TOTAL_TIMEOUT = 20    # 单条「生成+播放」整体超时（双保险）
# MAX_QUEUE_SIZE = 100      # 队列上限，超出后丢弃最旧的一条，避免无限堆积

# class TTSManager:
#     """
#     TTSManager 单例类
#     - 使用 edge-tts 将文字转语音
#     - 将生成的音频文件保存到程序目录下的 mp3 文件夹
#     - 使用单线程队列顺序播放语音，并确保临时文件被删除
#     - 支持根据 AppState 设置控制是否播报
#     - 生成环节带超时：网络异常/弱网时不会永久卡死消费者线程
#     """
#     _instance = None
#     _lock = Lock()  # 保证单例线程安全

#     def __new__(cls):
#         with cls._lock:
#             if cls._instance is None:
#                 cls._instance = super(TTSManager, cls).__new__(cls)
#                 # 初始化队列和工作线程
#                 cls._instance._queue = Queue()
#                 cls._instance._thread = Thread(target=cls._instance._worker, daemon=True)
#                 cls._instance._thread.start()
#                 # 初始化pygame音频系统
#                 pygame.mixer.init()
#                 # 创建一个待删除文件的列表
#                 cls._instance._files_to_delete = []
#                 # 控制标志
#                 cls._instance._is_running = True
#                 cls._instance._current_playing = None
#                 # 上次检查的状态
#                 cls._instance._last_voice_enabled = True
#             return cls._instance

#     async def _speak_async(self, text):
#         """
#         异步方法生成 TTS 并播放
#         """
#         # 检查语音播报是否启用
#         if not self._is_voice_enabled():
#             logging.info("语音播报已禁用，跳过播放")
#             return

#         # 获取程序根目录（区分开发/打包环境）
#         if getattr(sys, 'frozen', False):
#             # 打包后的可执行文件运行
#             base_dir = os.path.dirname(sys.executable)
#         else:
#             # 开发环境运行
#             base_dir = os.path.dirname(__file__)

#         # mp3 文件存放路径
#         mp3_dir = os.path.join(base_dir, "mp3")
#         if not os.path.exists(mp3_dir):
#             os.makedirs(mp3_dir)

#         # 临时文件名，放在 mp3 目录下
#         tmp_filename = os.path.join(mp3_dir, f"{int(time.time() * 1000)}.mp3")
        
#         try:
#             # 先尝试删除之前未能删除的文件
#             self._cleanup_files()
            
#             # 再次检查语音播报状态（可能在生成过程中被禁用）
#             if not self._is_voice_enabled():
#                 logging.info("语音播报在生成过程中被禁用，跳过播放")
#                 return
            
#             # 创建 edge-tts 对象
#             tts = edge_tts.Communicate(text, voice="zh-CN-XiaoyiNeural") # 语音模型
#             logging.info("开始生成语音: %s", text)
#             # 保存音频到文件（带超时：弱网/无网时不会永久卡死消费者线程）
#             try:
#                 await asyncio.wait_for(tts.save(tmp_filename), timeout=TTS_GEN_TIMEOUT)
#             except asyncio.TimeoutError:
#                 logging.error("语音生成超时（%ss），跳过本条: %s", TTS_GEN_TIMEOUT, text)
#                 self._delete_file(tmp_filename)
#                 return
#             except Exception as e:
#                 logging.error("语音生成失败: %s", e)
#                 self._delete_file(tmp_filename)
#                 return
#             logging.info("语音生成完成，准备播放")
            
#             # 最终检查语音播报状态（可能在保存过程中被禁用）
#             if not self._is_voice_enabled():
#                 logging.info("语音播报在保存过程中被禁用，跳过播放")
#                 self._delete_file(tmp_filename)
#                 return
            
#             # 设置当前播放文件
#             self._current_playing = tmp_filename
            
#             # 使用pygame播放音频
#             pygame.mixer.music.load(tmp_filename)
#             pygame.mixer.music.play()
            
#             # 等待播放完成，但会持续检查是否被禁用
#             while pygame.mixer.music.get_busy():
#                 # 检查是否应该停止播放
#                 if not self._is_voice_enabled():
#                     logging.info("语音播报在播放过程中被禁用，停止播放")
#                     pygame.mixer.music.stop()
#                     break
#                 pygame.time.Clock().tick(10)
                
#             # 明确停止播放并卸载音频，释放资源
#             pygame.mixer.music.stop()
#             pygame.mixer.music.unload()
            
#             # 重置当前播放文件
#             self._current_playing = None
            
#             # 短暂延迟，确保资源释放
#             time.sleep(0.1)
            
#             # 尝试删除文件
#             self._delete_file(tmp_filename)
            
#         except Exception as e:
#             logging.error(f"TTS 处理出错: {e}")
#             # 如果出错也将文件加入待删除列表
#             if os.path.exists(tmp_filename):
#                 self._files_to_delete.append(tmp_filename)
#             self._current_playing = None

#     def _is_voice_enabled(self):
#         """检查语音播报是否启用"""
#         try:
#             from app_state import AppState
#             return AppState.get_show_gift_voice()
#         except ImportError:
#             return True

#     def _check_and_clear_queue_if_disabled(self):
#         """检查状态变化，如果语音被禁用则清空队列"""
#         current_state = self._is_voice_enabled()
#         logging.info(f"当前语音播报状态: {current_state}")
#         # 如果状态从启用变为禁用
#         if self._last_voice_enabled and not current_state:
#             logging.info("语音播报被禁用，清空队列并停止播放")
#             self._clear_queue()
#             if pygame.mixer.music.get_busy():
#                 pygame.mixer.music.stop()
#                 pygame.mixer.music.unload()
#             if self._current_playing:
#                 self._delete_file(self._current_playing)
#                 self._current_playing = None
        
#         # 更新上次状态
#         self._last_voice_enabled = current_state
#         return current_state

#     def _clear_queue(self):
#         """清空队列中的所有待播放语音"""
#         while not self._queue.empty():
#             try:
#                 self._queue.get_nowait()
#                 self._queue.task_done()
#             except:
#                 break
#         logging.info(f"队列已清空，剩余 {self._queue.qsize()} 个任务")

#     def _delete_file(self, filename):
#         """尝试删除文件，失败则加入待删除列表"""
#         try:
#             if os.path.exists(filename):
#                 os.remove(filename)
#         except Exception as e:
#             logging.error(f"立即删除文件失败，将稍后重试: {e}")
#             self._files_to_delete.append(filename)
    
#     def _cleanup_files(self):
#         """清理之前未能删除的文件"""
#         if not self._files_to_delete:
#             return
            
#         files_remaining = []
#         for filename in self._files_to_delete:
#             try:
#                 # 先尝试直接删除
#                 if os.path.exists(filename):
#                     os.remove(filename)
#                     logging.info(f"成功删除之前的临时文件: {filename}")
#             except:
#                 try:
#                     # 如果直接删除失败，尝试移动到回收站
#                     if os.name == 'nt':  # Windows系统
#                         # import winshell
#                         # winshell.recycle_bin().move(filename)
#                         logging.info(f"文件已移至回收站: {filename}")
#                     else:
#                         # 非Windows系统，保留待下次尝试
#                         files_remaining.append(filename)
#                 except Exception as e:
#                     logging.error(f"清理文件 {filename} 失败: {e}")
#                     files_remaining.append(filename)
        
#         self._files_to_delete = files_remaining

#     def _worker(self):
#         """
#         队列工作线程，不断获取待播放文本并顺序播放
#         """
#         while True:
#             # 每次循环都检查状态变化
#             if not self._check_and_clear_queue_if_disabled():
#                 # 如果语音被禁用，等待一段时间再检查
#                 time.sleep(0.5)
#                 continue
                
#             text = self._queue.get()
#             try:
#                 # 再次检查是否应该处理这个语音
#                 if not self._is_voice_enabled():
#                     logging.info("语音播报已禁用，跳过队列中的语音")
#                     self._queue.task_done()
#                     continue
                
#                 try:
#                     asyncio.run(asyncio.wait_for(self._speak_async(text), timeout=TTS_TOTAL_TIMEOUT))
#                 except asyncio.TimeoutError:
#                     logging.error("单条语音处理整体超时，已跳过并继续下一条")
#             except Exception as e:
#                 logging.error(f"TTS 播放出错: {e}")
#             self._queue.task_done()

#     @classmethod
#     def speak(cls, text):
#         """
#         对外方法，将文本加入队列，顺序播放
#         """
#         instance = cls()
        
#         # 检查语音播报是否启用
#         if not instance._is_voice_enabled():
#             logging.info("语音播报已禁用，不加入队列")
#             return
        
#         # 队列保护：超过上限时丢弃最旧的一条，避免无限堆积拖垮内存与延迟
#         if instance._queue.qsize() >= MAX_QUEUE_SIZE:
#             try:
#                 instance._queue.get_nowait()
#             except Exception:
#                 pass
#         instance._queue.put(text)
#         logging.info(f"语音已加入队列，当前队列长度: {instance._queue.qsize()}")

#     @classmethod
#     def stop_all(cls):
#         """
#         停止所有语音播放并清空队列
#         """
#         instance = cls()
        
#         # 停止当前播放
#         if pygame.mixer.music.get_busy():
#             pygame.mixer.music.stop()
#             pygame.mixer.music.unload()
        
#         # 清空队列
#         instance._clear_queue()
        
#         # 删除当前播放的文件
#         if instance._current_playing and os.path.exists(instance._current_playing):
#             try:
#                 os.remove(instance._current_playing)
#             except:
#                 instance._files_to_delete.append(instance._current_playing)
#             instance._current_playing = None
        
#         logging.info("已停止所有语音播放并清空队列")


# # -----------------------------
# # 测试方法
# # -----------------------------
# def test_tts_sequence():
#     """
#     测试 TTS 顺序播放和状态切换
#     """
#     logging.info("测试开始：依次播放三条语音")
#     TTSManager.speak("第一条消息，测试顺序播放")
#     TTSManager.speak("第二条消息，应该在第一条播完后播放")
#     TTSManager.speak("第三条消息，最后播放")

#     # 模拟5秒后禁用语音
#     def disable_voice():
#         time.sleep(5)
#         logging.info("\n=== 模拟禁用语音播报 ===")
#         try:
#             from app_state import AppState
#             AppState.set_show_gift_voice(False)
#             AppState.set_show_msg_voice(False)
#             logging.info("语音播报已禁用，应该立即停止播放并清空队列")
#         except ImportError:
#             logging.error("无法导入 AppState，跳过状态设置测试")

#     # 启动禁用线程
#     Thread(target=disable_voice, daemon=True).start()

#     # 保证主线程不退出，给 TTS 播放留时间
#     try:
#         while True:
#             time.sleep(1)
#     except KeyboardInterrupt:
#         # 程序退出前最后尝试清理文件
#         TTSManager.stop_all()
#         logging.info("测试结束")

# if __name__ == "__main__":
#     test_tts_sequence()

import asyncio
import edge_tts  # type: ignore
import itertools
import os
import sys
import time
import logging

from threading import Thread, Lock
from queue import PriorityQueue, Empty

import pygame  # type: ignore


logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)


# ============================================================
# 配置
# ============================================================

# 单条 TTS 网络生成最大等待时间
TTS_GEN_TIMEOUT = 10

# 单条「生成 + 播放」最大等待时间
TTS_TOTAL_TIMEOUT = 20

# 最大队列数量
MAX_QUEUE_SIZE = 100

# TTS 声音
TTS_VOICE = "zh-CN-XiaoyiNeural"


class TTSManager:

    _instance = None
    _lock = Lock()

    def __new__(cls):

        with cls._lock:

            if cls._instance is None:

                logging.info("正在初始化 TTSManager...")

                cls._instance = super(TTSManager, cls).__new__(cls)

                instance = cls._instance

                # ====================================================
                # 1. 先初始化所有成员变量
                # ====================================================

                instance._queue = PriorityQueue()

                instance._files_to_delete = []

                instance._is_running = True

                instance._current_playing = None

                # 单调自增序号，仅用于同优先级内的 FIFO 排序
                instance._seq = itertools.count()

                # 获取当前真实状态
                try:
                    instance._last_voice_enabled = (
                        instance._is_voice_enabled()
                    )
                except Exception:
                    instance._last_voice_enabled = True

                # ====================================================
                # 2. 先初始化 pygame
                # ====================================================

                try:

                    pygame.mixer.init()

                    logging.info(
                        "pygame mixer 初始化成功"
                    )

                except Exception:

                    logging.exception(
                        "pygame mixer 初始化失败"
                    )

                # ====================================================
                # 3. 最后才启动 Worker
                # ====================================================

                instance._thread = Thread(
                    target=instance._worker,
                    daemon=True,
                    name="TTS-Worker"
                )

                instance._thread.start()

                logging.info(
                    "TTS Worker 已启动"
                )

            return cls._instance

    # ============================================================
    # AppState
    # ============================================================

    def _is_voice_enabled(self):

        try:

            from core.app_state import AppState

            return AppState.get_show_gift_voice() # 测试用
            # return True

        except ImportError:

            logging.warning("无法导入 AppState，默认启用语音")

            return True

        except Exception:

            logging.exception(
                "读取 AppState 语音状态失败"
            )

            return True

    # ============================================================
    # TTS 生成 + 播放
    # ============================================================

    async def _speak_async(self, text):

        if not self._is_voice_enabled():

            logging.info(
                "语音播报已禁用，跳过：%s",
                text
            )

            return

        # ========================================================
        # 获取程序目录
        # ========================================================

        if getattr(sys, "frozen", False):

            base_dir = os.path.dirname(
                sys.executable
            )

        else:

            base_dir = os.path.dirname(
                os.path.abspath(__file__)
            )

        # ========================================================
        # mp3 目录
        # ========================================================

        mp3_dir = os.path.join(
            base_dir,
            "mp3"
        )

        os.makedirs(
            mp3_dir,
            exist_ok=True
        )

        # 使用纳秒时间，避免同一毫秒产生相同文件名
        tmp_filename = os.path.join(
            mp3_dir,
            f"{time.time_ns()}.mp3"
        )

        try:

            # ====================================================
            # 清理旧文件
            # ====================================================

            self._cleanup_files()

            # ====================================================
            # 创建 TTS
            # ====================================================

            logging.info(
                "开始生成语音：%s",
                text
            )

            tts = edge_tts.Communicate(
                text,
                voice=TTS_VOICE
            )

            # ====================================================
            # 生成 MP3
            # ====================================================

            try:

                await asyncio.wait_for(
                    tts.save(tmp_filename),
                    timeout=TTS_GEN_TIMEOUT
                )

            except asyncio.TimeoutError:

                logging.error(
                    "TTS 生成超时 %s 秒：%s",
                    TTS_GEN_TIMEOUT,
                    text
                )

                self._delete_file(
                    tmp_filename
                )

                return

            except Exception:

                logging.exception(
                    "edge-tts 生成失败：%s",
                    text
                )

                self._delete_file(
                    tmp_filename
                )

                return

            # ====================================================
            # 检查文件
            # ====================================================

            if not os.path.exists(tmp_filename):

                logging.error(
                    "TTS 已返回，但 MP3 文件不存在：%s",
                    tmp_filename
                )

                return

            file_size = os.path.getsize(
                tmp_filename
            )

            logging.info(
                "语音生成完成：%s，大小=%s bytes",
                tmp_filename,
                file_size
            )

            if file_size <= 0:

                logging.error(
                    "TTS 文件为空"
                )

                self._delete_file(
                    tmp_filename
                )

                return

            # ====================================================
            # 再次检查开关
            # ====================================================

            if not self._is_voice_enabled():

                logging.info(
                    "语音生成过程中被关闭，跳过播放"
                )

                self._delete_file(
                    tmp_filename
                )

                return

            # ====================================================
            # 设置当前播放文件
            # ====================================================

            self._current_playing = tmp_filename

            # ====================================================
            # pygame 加载
            # ====================================================

            logging.info(
                "pygame 开始加载：%s",
                tmp_filename
            )

            try:

                pygame.mixer.music.load(
                    tmp_filename
                )

            except Exception:

                logging.exception(
                    "pygame 加载 MP3 失败：%s",
                    tmp_filename
                )

                return

            # ====================================================
            # 开始播放
            # ====================================================

            logging.info(
                "开始播放语音：%s",
                text
            )

            pygame.mixer.music.play()

            # ====================================================
            # 等待播放完成
            # ====================================================

            clock = pygame.time.Clock()

            while pygame.mixer.music.get_busy():

                # 语音被关闭
                if not self._is_voice_enabled():

                    logging.info(
                        "语音播放过程中被关闭，停止播放"
                    )

                    pygame.mixer.music.stop()

                    break

                clock.tick(20)

            logging.info(
                "语音播放结束：%s",
                text
            )

            # ====================================================
            # 释放 pygame 音频
            # ====================================================

            try:

                pygame.mixer.music.stop()

            except Exception:
                pass

            try:

                pygame.mixer.music.unload()

            except Exception:

                logging.debug(
                    "pygame unload 失败",
                    exc_info=True
                )

            # ====================================================
            # 重置当前播放
            # ====================================================

            self._current_playing = None

            # 给系统一点时间释放文件
            await asyncio.sleep(0.1)

            # ====================================================
            # 删除 MP3
            # ====================================================

            self._delete_file(
                tmp_filename
            )

        except Exception:

            logging.exception(
                "TTS 处理出现异常：%s",
                text
            )

            # 停止播放
            try:

                pygame.mixer.music.stop()

            except Exception:
                pass

            try:

                pygame.mixer.music.unload()

            except Exception:
                pass

            # 文件加入待删除
            if os.path.exists(tmp_filename):

                self._files_to_delete.append(
                    tmp_filename
                )

            self._current_playing = None

    # ============================================================
    # Worker
    # ============================================================

    def _worker(self):

        logging.info(
            "========================================"
        )

        logging.info(
            "TTS Worker线程正式启动"
        )

        logging.info(
            "========================================"
        )

        while self._is_running:

            try:

                # ==================================================
                # 检查语音开关
                # ==================================================

                current_state = (
                    self._is_voice_enabled()
                )

                # 状态从开启 -> 关闭
                if (
                    self._last_voice_enabled
                    and not current_state
                ):

                    logging.info(
                        "语音被关闭，清空队列"
                    )

                    self._clear_queue()

                    try:

                        if pygame.mixer.music.get_busy():

                            pygame.mixer.music.stop()

                            try:
                                pygame.mixer.music.unload()
                            except Exception:
                                pass

                    except Exception:

                        logging.exception(
                            "停止当前语音失败"
                        )

                    if self._current_playing:

                        self._delete_file(
                            self._current_playing
                        )

                        self._current_playing = None

                self._last_voice_enabled = current_state

                # ==================================================
                # 未开启语音
                # ==================================================

                if not current_state:

                    time.sleep(0.5)

                    continue

                # ==================================================
                # 获取队列
                #
                # 这里必须使用 timeout
                # 不能永久阻塞
                # ==================================================

                try:

                    # PriorityQueue 元素为 (priority, seq, text)
                    _, _, text = self._queue.get(
                        timeout=0.5
                    )

                except Empty:

                    continue

                # ==================================================
                # 成功取到任务
                # ==================================================

                logging.info(
                    "TTS Worker取出语音：%s",
                    text
                )

                logging.info(
                    "当前剩余队列：%s",
                    self._queue.qsize()
                )

                try:

                    # 再检查一次
                    if not self._is_voice_enabled():

                        logging.info(
                            "取出后发现语音已关闭，跳过"
                        )

                        continue

                    # ==================================================
                    # 执行 TTS
                    # ==================================================

                    try:

                        asyncio.run(
                            asyncio.wait_for(
                                self._speak_async(text),
                                timeout=TTS_TOTAL_TIMEOUT
                            )
                        )

                    except asyncio.TimeoutError:

                        logging.error(
                            "单条语音整体超时 %s 秒：%s",
                            TTS_TOTAL_TIMEOUT,
                            text
                        )

                except Exception:

                    logging.exception(
                        "Worker处理语音失败：%s",
                        text
                    )

                finally:

                    # 非常重要
                    self._queue.task_done()

            except Exception:

                # ==================================================
                # Worker最外层保护
                #
                # 无论什么异常，Worker都不能死
                # ==================================================

                logging.exception(
                    "TTS Worker发生未捕获异常"
                )

                time.sleep(1)

        logging.info(
            "TTS Worker线程退出"
        )

    # ============================================================
    # 清空队列
    # ============================================================

    def _clear_queue(self):

        count = 0

        while True:

            try:

                self._queue.get_nowait()

                self._queue.task_done()

                count += 1

            except Empty:

                break

            except Exception:

                break

        logging.info(
            "TTS队列已清空，共清除 %s 条",
            count
        )

    # ============================================================
    # 删除文件
    # ============================================================

    def _delete_file(self, filename):

        if not filename:
            return

        try:

            if os.path.exists(filename):

                os.remove(filename)

                logging.debug(
                    "删除TTS文件：%s",
                    filename
                )

        except Exception:

            logging.error(
                "删除TTS文件失败，加入待清理列表：%s",
                filename,
                exc_info=True
            )

            if filename not in self._files_to_delete:

                self._files_to_delete.append(
                    filename
                )

    # ============================================================
    # 清理历史文件
    # ============================================================

    def _cleanup_files(self):

        if not self._files_to_delete:
            return

        remaining = []

        for filename in self._files_to_delete:

            try:

                if os.path.exists(filename):

                    os.remove(filename)

                    logging.info(
                        "清理旧TTS文件：%s",
                        filename
                    )

            except Exception:

                remaining.append(
                    filename
                )

        self._files_to_delete = remaining

    # ============================================================
    # 对外：播放语音
    # ============================================================

    @classmethod
    def speak(cls, text, priority=False):
        """把文本加入语音队列。

        priority=True 时插队到队列最前（高价值礼物优先播报），
        但**不打断**当前正在播放的语音——等当前这条播完，
        下一条就是大礼物播报。
        普通语音在队列满时丢弃本条；优先语音永远入队、绝不丢失。
        """

        if not text:
            return

        instance = cls()

        # ========================================================
        # 检查开关
        # ========================================================

        if not instance._is_voice_enabled():

            logging.info(
                "语音已关闭，不加入队列"
            )

            return

        # ========================================================
        # 高价值礼物：插队到队列最前（不打断当前播放）
        # ========================================================

        if priority:

            instance._queue.put(
                (0, next(instance._seq), text)
            )

            logging.info(
                "优先语音已加入队列（不打断当前播放）：%s（剩余 %s）",
                text,
                instance._queue.qsize()
            )

            return

        # ========================================================
        # 普通语音：队列满则丢弃本条，避免无限堆积
        # ========================================================

        if instance._queue.qsize() >= MAX_QUEUE_SIZE:

            logging.warning(
                "TTS队列已满，丢弃普通语音：%s",
                text
            )

            return

        instance._queue.put(
            (1, next(instance._seq), text)
        )

        logging.info(
            "语音已加入队列，当前队列长度：%s",
            instance._queue.qsize()
        )

    # ============================================================
    # 停止全部
    # ============================================================

    @classmethod
    def stop_all(cls):

        instance = cls()

        logging.info(
            "准备停止所有语音"
        )

        try:

            pygame.mixer.music.stop()

        except Exception:
            pass

        try:

            pygame.mixer.music.unload()

        except Exception:
            pass

        # 清空队列
        instance._clear_queue()

        # 删除当前播放文件
        if instance._current_playing:

            instance._delete_file(
                instance._current_playing
            )

            instance._current_playing = None

        logging.info(
            "所有语音已停止"
        )


# ================================================================
# 测试
# ================================================================

def test_tts_sequence():

    logging.info(
        "开始 TTS 测试"
    )

    TTSManager.speak(
        "第一条消息，测试语音播放"
    )

    TTSManager.speak(
        "第二条消息，测试顺序播放"
    )

    TTSManager.speak(
        "第三条消息，测试队列播放"
    )

    try:

        while True:

            time.sleep(1)

    except KeyboardInterrupt:

        TTSManager.stop_all()


if __name__ == "__main__":

    test_tts_sequence()