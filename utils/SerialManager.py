import serial # type: ignore
import threading
import queue
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

class SerialManager:
    def __init__(self, port='COM1', baudrate=19200, timeout=1, min_interval=1.5):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.min_interval = min_interval
        
        # ==============================================
        # 线程相关的核心组件
        # ==============================================
        
        # 1. 控制线程运行的标志
        self._running = True  # True=线程继续运行，False=线程退出
        
        # 2. 队列：主线程放指令，工作线程取指令
        self._cmd_queue = queue.Queue()  # 线程安全的队列
        
        # 3. 记录上次发送时间
        self._last_send_time = 0
        
        # 4. 创建并启动工作线程
        self._thread = threading.Thread(
            target=self._worker,  # 线程要执行的函数
            daemon=True          # 主程序退出时，这个线程也自动退出
        )
        self._thread.start()  # 启动线程，开始执行 _worker 函数
        
        print("✅ 工作线程已启动，正在后台等待指令...")

    def connect(self, port=None, baudrate=None):
        # 串口连接逻辑（与线程无关）
        if port:
            self.port = port
        if baudrate:
            self.baudrate = baudrate

        if self.ser and self.ser.is_open:
            self.ser.close()

        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            logging.info(f"串口 {self.port} 已打开")
            return True
        except Exception as e:
            logging.error(f"串口连接失败: {e}")
            self.ser = None
            return False

    def close(self):
        # ==============================================
        # 关闭时的线程处理
        # ==============================================
        print("🔄 正在关闭串口管理器...")
        
        # 1. 告诉工作线程要退出了
        self._running = False
        
        # 2. 等待工作线程结束（最多等2秒）
        self._thread.join(timeout=2)
        print("✅ 工作线程已结束")
        
        # 3. 关闭串口
        if self.ser and self.ser.is_open:
            self.ser.close()
        logging.info("串口已关闭")

    @staticmethod
    def _with_xor(hex_str: str) -> bytes:
        # 异或校验（与线程无关）
        data = bytes.fromhex(hex_str.replace(" ", ""))
        checksum = 0
        for b in data:
            checksum ^= b
        return data + bytes([checksum])

    def send_command(self, hex_cmd: str):
        # ==============================================
        # 主线程调用这个函数（非阻塞）
        # ==============================================
        print(f"📤 主线程：收到发送请求 {hex_cmd}")
        
        # 1. 处理指令（加上异或校验）
        frame = self._with_xor(hex_cmd)
        
        # 2. 放入队列（线程安全操作）
        self._cmd_queue.put(frame.hex().upper())
        print(f"📋 主线程：指令已放入队列，当前队列大小: {self._cmd_queue.qsize()}")
        
        # 3. 立即返回！不等待发送完成
        print(f"✅ 主线程：send_command() 立即返回，不阻塞")

    def _worker(self):
        # ==============================================
        # 这是工作线程的主函数
        # 这个函数在后台一直循环运行
        # ==============================================
        print("🚀 工作线程：开始运行...")
        
        while self._running:  # 只要 _running 是 True 就一直循环
            try:
                # print("👀 工作线程：检查队列是否有新指令...")
                
                # 1. 从队列取指令（等待0.1秒，没有就抛出 Empty 异常）
                frame_hex = self._cmd_queue.get(timeout=0.1)
                print(f"📥 工作线程：从队列取到指令 {frame_hex}")
                
                # 2. 检查发送间隔
                current_time = time.time()
                elapsed_time = current_time - self._last_send_time
                
                if elapsed_time < self.min_interval:
                    wait_time = self.min_interval - elapsed_time
                    print(f"⏰ 工作线程：距离上次发送仅 {elapsed_time:.2f}s，需要等待 {wait_time:.2f}s")
                    time.sleep(wait_time)  # 工作线程休眠，主线程不受影响
                    print(f"✅ 工作线程：等待完成，现在可以发送了")
                
                # 3. 实际发送
                if self.ser and self.ser.is_open:
                    frame_bytes = bytes.fromhex(frame_hex)
                    self.ser.write(frame_bytes)
                    self._last_send_time = time.time()
                    print(f"📡 工作线程：指令已发送 {frame_bytes.hex().upper()}")
                    logging.info(f"TX: {frame_bytes.hex().upper()}")
                else:
                    print("⚠️ 工作线程：串口未打开，无法发送")
                    
            except queue.Empty:
                # 队列为空，继续循环等待
                pass
            except Exception as e:
                print(f"❌ 工作线程：发送时出错 {e}")

            # 4. 顺便读取回显数据
            try:
                if self.ser and self.ser.is_open and self.ser.in_waiting:
                    rx = self.ser.read(self.ser.in_waiting)
                    print(f"📨 工作线程：收到回显 {rx.hex().upper()}")
                    logging.info(f"RX: {rx.hex().upper()}")
            except Exception as e:
                print(f"❌ 工作线程：读取回显出错 {e}")
        
        print("🛑 工作线程：_running=False，退出循环")

    def is_open(self):
        return self.ser and self.ser.is_open
    
    def get_queue_size(self):
        return self._cmd_queue.qsize()


# 使用示例，展示线程工作过程
if __name__ == "__main__":
    print("=== 创建串口管理器 ===")
    serial_manager = SerialManager(min_interval=1.2)  # 2秒间隔便于观察
    
    print("\n=== 连接串口 ===")
    # serial_manager.connect()  # 如果没有真实串口，注释掉这行
    
    print("\n=== 快速发送多条指令 ===")
    print("主线程：开始快速发送指令...")
    
    serial_manager.send_command("A101")
    print("主线程：第1条指令已提交")
    
    serial_manager.send_command("A102")
    print("主线程：第2条指令已提交")
    
    serial_manager.send_command("A103")
    print("主线程：第3条指令已提交")
    
    print("主线程：所有指令都提交完了，现在可以做其他事情...")
    print("主线程：比如更新UI、处理用户输入等...")
    
    # 主线程等待，观察工作线程的行为
    print("\n=== 等待工作线程处理完成 ===")
    time.sleep(20)
    
    print("\n=== 关闭管理器 ===")
    serial_manager.close()

serial_manager = SerialManager()