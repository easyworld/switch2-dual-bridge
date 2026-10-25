# Switch 2 Dual Bridge

基于 **ESP32-S3** 的 Switch 2 USB 键盘与手柄桥接器。电脑通过 Wi-Fi 发送输入，板子通过原生 USB 同时提供两个独立 HID 接口；Python 桌面客户端提供文本输入、可编辑的手柄键位图及连接管理。

## 功能

- USB 键盘与手柄同时连接，切换客户端标签页即可操作。
- ASCII 文本、Tab、换行、US/JIS 布局，以及 SmileBASIC 逐行输入和缩进处理。
- Switch Pro 风格手柄图，支持按钮、十字键、双摇杆、组合键与可保存的电脑键位映射。
- 异步实时手柄输入：按下保持、松开释放；支持组合键与可选按钮长按连发，发送期间继续接收操作。
- 可选 BLE 唤醒广播，需配置自己设备的地址和原始广播数据。
- 停止并释放全部按键、主动断开和恢复主机 USB 连接；Wi-Fi 控制通道保持连接。
- Windows 无控制台启动；错误日志自动轮转。

本项目通过 USB 提供普通 HID 输入。BLE 仅用于已配置设备的唤醒广播，不提供蓝牙手柄输入。不包含体感、震动、NFC 或 C/GL/GR 按键。

## 硬件与环境

- ESP32-S3，当前构建配置为 16 MB Flash、DIO、USB OTG、CDC 关闭、PSRAM 关闭。
- 支持数据传输的 USB 线：板子**原生 USB 口**连接 Switch 2 底座的 USB-A 口。
- Switch 2 设置中开启 Nintendo Switch Pro 手柄的有线通信。
- Python 3.12，含 Tkinter；客户端仅使用 Python 标准库。
- Arduino IDE / Arduino CLI，Arduino ESP32 core **3.3.7**。BLE 使用支持包内置 NimBLE。

## 首次配置

本仓库不包含本机密钥、Wi-Fi 密码、个人草稿、设备唤醒参数或预编译固件。

```powershell
Copy-Item client-config.example.json client-config.json
Copy-Item firmware/Switch2Dual/secrets.example.h firmware/Switch2Dual/secrets.h
Copy-Item firmware/Switch2Dual/WakeConfig.example.h firmware/Switch2Dual/WakeConfig.h
```

1. 编辑 `secrets.h`：设置热点名称、热点密码（至少 8 位）和随机 API 密钥。
2. 编辑 `client-config.json`：`api_key` 与固件一致，`url` 指向板子。连接板子热点时默认为 `http://192.168.4.1`。
3. 若使用 BLE 唤醒，在 `WakeConfig.h` 中填写自己设备的蓝牙地址与完整 31 字节原始广播数据。示例中的全零值不能唤醒设备；不使用此功能时无需点击唤醒按钮。
4. 编译、刷写固件，然后启动客户端。

这些本地配置均已加入 `.gitignore`。HTTP API 使用 `X-Key` 认证，适用于可信局域网；不要把板子端口暴露到互联网。

## 启动

Windows 双击 **`start-dual.cmd`**。脚本通过 `pythonw.exe` 打开 `start-dual.pyw`，无需 VBS 文件，不保留黑色控制台。

也可在终端启动：

```powershell
python remote.py
```

点击“连接 / 查询”，等待 USB 键盘、手柄就绪。首次枚举后约有一秒稳定等待。连接问题优先检查数据线、原生 USB 端口和主机设置。

启动失败会显示弹窗；界面异常详情写入 `remote-errors.log`，日志限制大小并自动轮转。

## 键盘与文本

支持每次 1–16384 个 ASCII 字符、Tab 和换行，布局可选 US/JIS。默认按下、释放各等待 30 ms，换行额外等待 200 ms；漏字时可以增加等待。

在 SmileBASIC 的空白程序编辑区使用“逐行输入”，避免自动缩进累积；普通输入框、文件名或 Direct 控制台应取消此选项。发送正文前会检查并关闭 CapsLock，快捷键保持原样。发送完成后仍需在主机上保存文件。

关闭窗口会将电脑端草稿保存到 `last-draft.txt`，它不等于主机上已保存的程序。

## 手柄与键位映射

手柄图上行显示主机按键，下行显示电脑键位。编辑模式中点击部位后按一个键，点击“保存映射”生效；同一电脑键只能绑定一个部位。映射保存在本地 `gamepad-keymap.json`。

| 手柄操作 | 默认电脑键位 |
|---|---|
| A / B / X / Y | J / K / U / I |
| L / R / ZL / ZR | Q / E / Z / C |
| 十字键上 / 下 / 左 / 右 | W / S / A / D |
| 左摇杆四方向 | 方向键 |
| 右摇杆上 / 下 / 左 / 右 | 8 / 2 / 4 / 6 |
| L3 / R3 | F / G |
| − / ＋ / HOME / 截图 | - / = / H / P |
| 回中 / L+R | Space / Enter |

字母不区分大小写。支持数字、方向键、功能键与常用单键；Ctrl/Alt 组合键不用于映射，Esc 用于取消选键或停止输入。仅在手柄区域获得焦点时接收游戏按键。

### 实时输入（固件 dual-1.3.0 及以上）

键盘或鼠标按下后持续保持对应状态，松开即提交释放；支持同时按多个按钮，以及摇杆与按钮组合。相反方向同时按下时回中。发送在独立后台线程中执行，手柄图不会因为正在发送而整张变灰，也不会阻塞键盘文本通道。

勾选“按钮长按连发”后，当前按住的按钮会按周期交替按下、释放，默认完整周期 120 ms，可设为 50–1000 ms。十字键与摇杆仍保持持续按住。默认关闭连发，行为与普通手柄一致。实时模式不使用“兼容点击 ms”参数。

切换标签页、进入编辑模式或手柄区域失去焦点时自动提交释放。Esc、“停止 / 全部释放”、关闭窗口及主动断开会清空待发送操作并请求板子释放。

短按的按下、松开顺序会被保留；每个状态先得到 USB 发送完成确认，再至少保持 16 ms，避免快速短按在一个 USB 周期内被吞掉。客户端最多保留 64 个待发送状态，超过 500 ms 的积压会停止并丢弃；板子最多保留 16 个状态，过期队列同样自动停止。网络异常后不会补放一长串旧操作。

后台每 100 ms 续租一次。断网、客户端退出或不再收到有效数据包约 600 ms 后，固件会清空队列、回中并递增 epoch，拒绝迟到的旧会话请求。USB 主机若停止接收，物理释放报告需等通信恢复后才能送达。

### 旧固件兼容模式

旧固件仍可使用客户端，但每次只能发送一个 50–2000 ms 的定时输入，默认 150 ms，长按不连发。**要启用实时输入，需要重新编译并刷入新版固件，单独更新客户端不会改变板子的协议。** 修改前版本保存在 Git 标签 `baseline-before-async`。

## 停止、连接与唤醒

- Esc 或“停止 / 全部释放”：取消任务并释放输入，停止唤醒广播。在手柄编辑模式中，Esc 仅取消当前选键。
- “断开所有连接”：先释放输入，再断开 USB 并停止广播，保留 Wi-Fi 与 HTTP。
- “重新连接主机”：恢复 USB 枚举；不会自动发送唤醒广播。
- “唤醒 Switch 2”：发送一次 1 秒 BLE 广播。广播发送成功不等于主机已经醒来，以主机画面及 USB 状态为准。

超时后先查询状态，客户端不会自动重复不确定是否执行的定时按键或唤醒操作。

## 编译与刷写

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
```

构建脚本使用 Windows Arduino IDE 所附 CLI 和 ESP32 core 3.3.7，将输出写入本地 `binaries/`，并清理临时构建目录。

板子原生 USB 接电脑，按住 BOOT，短按 RESET/EN，松开 BOOT，确认下载串口和目标板 MAC 后执行：

```powershell
python -m pip install esptool
powershell -NoProfile -ExecutionPolicy Bypass -File .\flash.ps1 -Port COM11 -ExpectedMac 'AA:BB:CC:DD:EE:FF'
```

也可复制 `flash.example.json` 为 `flash.local.json` 并填写目标 MAC，之后只传 `-Port`。脚本校验 MAC 后才写入固件。串口与 MAC 均需换成自己的设备值。刷写后若仍在下载模式，松开 BOOT，再按一次 RESET/EN。

## 命令行

```powershell
python remote.py status
python remote.py text 'Aa09'
python remote.py pulse --buttons A --hold 150
python remote.py pulse --buttons L+R --hold 300
python remote.py pulse --dpad UP --hold 150
python remote.py pulse --lx 255 --hold 200
python remote.py wake
python remote.py cancel
python remote.py disconnect
python remote.py connect
```

使用指定地址：`python remote.py --url http://192.168.4.1 status`。

## HTTP API

所有接口使用 `X-Key` 认证。定时输入使用独立 `X-Job` 标识，固件检查重复任务及 epoch。

| 接口 | 用途 |
|---|---|
| `GET /status` | USB、键盘、手柄、BLE 与连接控制状态 |
| `POST /text`、`POST /tap` | 文本与键盘单键 |
| `POST /pad` | 有限时长的手柄输入 |
| `POST /pad/stream` | 有序的实时状态更新与续租 |
| `POST /cancel` | 停止输入并释放 |
| `POST /disconnect`、`POST /connect` | USB 连接控制 |
| `POST /switch2/wakeup` | 一次 1 秒 BLE 唤醒广播 |

双接口模式中，键盘状态位于根对象，手柄状态位于 `gamepad`；不要混用两者的 busy、job 或完成标志。

实时能力由 `gamepad.stream.supported` 标识。`POST /pad/stream` 使用表单字段 `session`（32 位十六进制随机会话）、`sequence`（从 0 连续递增）、`epoch` 和 `frames`。每帧格式为 `buttons,dpad,lx,ly,rx,ry,duration_ms`，多帧以分号分隔，每包最多 8 帧；空 `frames` 用于续租。duration 范围 8–200 ms，客户端实时按键使用 16 ms。

成功响应包含 `accepted`、`next_sequence`、`queued`、`capacity`。重复的最后一包不会重复入队，也不延长租期；会话、序号、epoch、队列容量和 USB 就绪状态均由固件校验。客户端遇到超时或不确定确认会停止，不自动重发按键。重新连接时不会接管仍有效的其他会话。实时会话存活期间保持对手柄的独占使用；使用旧的命令行 `pulse` 前先在 GUI 停止输入。

## 开发与测试

```powershell
python -X utf8 -B -m unittest discover -s tests -v
```

协议测试通过本机 HTTP 服务运行，不向真实主机发送输入。Tk GUI 测试在 Windows 上运行，覆盖查询与输入并发、停止、恢复连接和刷新异常后的按钮恢复。

实时输入测试还覆盖慢请求期间持续收键、连续短按顺序、长按心跳、连发释放边沿、队列溢出、过期输入、epoch 变化和未知会话隔离。

固件队列逻辑可在 Visual Studio 开发人员命令提示符中独立验证：

```bat
mkdir work
cl /EHsc /std:c++17 tests/test_pad_stream.cpp /Fe:work/test_pad_stream.exe /Fo:work/test_pad_stream.obj
work\test_pad_stream.exe
```

此测试覆盖 USB 确认前不跳过短按、重复包、乱序、陈旧确认、队列容量及计时器溢出。完整硬件验证仍需刷入固件后在目标主机上进行。

| 路径 | 说明 |
|---|---|
| `remote.py` | GUI、命令行与设备管理 |
| `pro_controller.py` | 手柄图与键位编辑 |
| `client.py`、`controller.py` | 键盘与手柄客户端协议 |
| `pad_stream.py` | 独立发送线程、队列、续租与故障恢复 |
| `firmware/Switch2Dual/` | 双 USB HID 与 BLE 源码 |
| `tests/` | 协议与 GUI 测试 |
| `build.ps1`、`flash.ps1` | 构建与目标校验刷写 |

第三方手柄描述符的 MIT 许可保留在 `SwitchDescriptor.h` 中，TinyUSB 许可见 `TINYUSB-LICENSE.txt`。本项目与 Nintendo 无隶属关系。
