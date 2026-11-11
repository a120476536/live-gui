# vJoy 驱动安装与配置（MUGEN 弹幕驱动）

本方案用 **vJoy 虚拟手柄** 把弹幕/礼物变成「真实手柄按键」直接发进 MUGEN，
绕开键盘 RawInput 过滤（AHK / pynput / SendInput 都失败于此）。

## 1. 安装 vJoy 驱动
1. 下载 vJoy：https://sourceforge.net/projects/vjoystick/ （最新版即可）
2. 安装时勾选 **Configure vJoy** 和 **vJoy Monitor**（方便排错）。
3. 安装完**重启一次电脑**（vJoy 是内核驱动，重启后才生效）。

## 2. 配置两个虚拟手柄
1. 开始菜单打开 **Configure vJoy**。
2. 在设备列表里启用 **2 个设备**（Device 1、Device 2）。
   - MUGEN 把第一个检测到的手柄当 **P1**，第二个当 **P2**。
3. 每个设备设置：
   - **Axes**：保留默认（至少含 X、Y）
   - **Buttons**：设 **12**（覆盖用到的 1~11 号按钮，留余量）
   - POVs：默认即可
4. 点 **Apply**、**OK**。

## 3. 安装 Python 绑定 pyvjoy
用本机系统 Python312（live-gui 用的那个）：
```
%LOCALAPPDATA%\Programs\Python\Python312\python.exe -m pip install pyvjoy
```
> 若 import pyvjoy 报「找不到 vJoyInterface.dll」：
> 从 vJoy 安装目录（如 `C:\Program Files\vJoy\x64\vJoyInterface.dll`）
> 复制 `vJoyInterface.dll` 到 pyvjoy 包目录
> （如 `C:\Users\<你>\AppData\Local\Programs\Python\Python312\Lib\site-packages\pyvjoy\`）。
> 注意 Python 是 64 位就复制 x64 的 dll。

## 4. 最小验证（不连 MUGEN 也能测）
```
python utils/vjoy_driver.py
```
输出 `device 1 (A) OK` / `device 2 (B) OK` 即驱动通了。
（也可用 vJoy Monitor / Pointy's Joystick Test 看按钮是否被按下）

## 5. 跑起来
确保 live-gui 主程序已运行（WS 8765 在听），然后：
- 一键：双击 `scripts/start_mugen_danmaku.bat`（启动 MUGEN 对战 + 桥）
- 或单独：`scripts/run_mugen_bridge.bat`（只启动桥，MUGEN 另行启动）

验证驱动：
```
python utils/danmaku_mock.py --gift 玫瑰
```
看 `mugen_bridge.log`：出现 `[bridge] vJoy 已连接` + `[ok] 礼物:玫瑰 -> B/anfu`
且 MUGEN 里八神庵（P2）出暗拂。

## 6. 排错
- **bridge 日志 [warn] vJoy 初始化失败**：驱动没装 / 没重启 / 设备数不够 / dll 缺失 → 回到 1~3 步。
- **P1/P2 反了**（招式打到了另一边）：MUGEN 手柄枚举顺序问题。改
  `data/mugen_config.json` 的 `vjoy.p1_device` / `p2_device` 对调（如 2/1），重启桥。
- **权限报错（拒绝访问）**：vJoy 偶尔需管理员访问设备接口。把
  `start_mugen_danmaku.bat` / 桥以管理员身份运行（右键→以管理员身份运行）。
- **手动 WASD/方向键不动**：与 vJoy 无关（键盘走 MUGEN 的 Keyboard 输入）。
  检查 MUGEN 是否在前台、是否被别的窗口遮挡；这是普通键盘问题。
- **招式偶尔不触发**：适当调大 `data/mugen_config.json` 的 `vjoy.step_ms`（如 60~80），
  让斜方向输入更稳；或调大 `cooldown_ms` 避免连发冲突。

## 按钮映射（与 mugen.cfg [P1]/[P2 Joystick] 一致）
| 逻辑键 | 按钮号 | 含义 |
|--------|--------|------|
| U | 1 | 上/跳 |
| D | 2 | 下/蹲 |
| L | 3 | 左 |
| R | 4 | 右 |
| a | 5 | A 键 |
| b | 6 | B 键 |
| c | 7 | C 键 |
| x | 8 | X 键（八神庵招式触发键） |
| y | 9 | Y 键 |
| z | 10 | Z 键 |
| start | 11 | Start |
