# HTTP 协议

所有接口通过 `X-Key` 请求头认证，使用本地配置中的 API 密钥。接口运行于板子的 HTTP 服务，适用于可信局域网。

## 接口

| 方法与路径 | 用途 |
|---|---|
| `GET /status` | USB、键盘、手柄、BLE 和连接控制状态 |
| `POST /text`、`POST /tap` | 文本输入与键盘单键 |
| `POST /pad` | 有限时长的手柄输入 |
| `POST /pad/stream` | 有序实时状态更新与续租 |
| `POST /cancel` | 停止输入并释放 |
| `POST /disconnect`、`POST /connect` | USB 连接控制 |
| `POST /switch2/wakeup` | 一次 1 秒 BLE 唤醒广播 |

定时输入使用独立的 `X-Job` 标识；固件检查重复任务及 epoch。客户端不会自动重发执行结果不确定的定时输入或唤醒请求。

## 状态

双接口模式中，键盘状态位于根对象，手柄状态位于 `gamepad`。两者的 `busy`、`job` 和完成标志分别独立。

| 字段 | 含义 |
|---|---|
| `firmware`、`boot_id` | 固件版本与本次启动标识 |
| `usb_mounted`、`usb_suspended` | 主机是否已枚举 USB、是否处于挂起状态 |
| `input_ready`、`gamepad.input_ready` | 对应接口是否可以接收输入 |
| `gamepad.epoch` | 手柄输入代次；取消、断开或实时输入超时后改变 |
| `gamepad.stream.supported` | 是否支持实时输入协议 |
| `gamepad.stream.active` | 是否存在有效实时输入会话 |
| `gamepad.stream.queued` | 板子待完成的状态数量 |
| `gamepad.stream.next_sequence` | 下一个数据包序号 |
| `connection_control.usb_enabled` | USB 是否主动启用 |
| `wake` | BLE 初始化、广播、累计次数和错误状态 |

实时会话存活期间独占手柄通道，键盘通道独立工作。使用有限时长的 `pulse` 命令前，应先停止 GUI 实时输入。

## 实时输入

`POST /pad/stream` 使用 `application/x-www-form-urlencoded`，包含以下字段：

| 字段 | 格式 |
|---|---|
| `session` | 32 位十六进制随机会话标识 |
| `sequence` | 从 0 开始连续递增的包序号 |
| `epoch` | 最近状态中的手柄代次 |
| `frames` | 按顺序执行的状态，以分号分隔；每包最多 8 帧 |

每帧格式为：

```text
buttons,dpad,lx,ly,rx,ry,duration_ms
```

- `buttons`：14 位按钮掩码，范围 0–16383。
- `dpad`：上、右上、右、右下、下、左下、左、左上分别为 0–7；回中为 15。
- `lx,ly,rx,ry`：范围 0–255，128 为回中。
- `duration_ms`：范围 8–200 ms；客户端实时按键使用 16 ms。

A 按下再释放的帧数据示例：

```text
4,15,128,128,128,128,16;0,15,128,128,128,128,16
```

空的 `frames` 用于续租，不能创建新会话。成功响应示例：

```json
{"accepted":true,"next_sequence":1,"queued":2,"capacity":16}
```

固件校验会话归属、连续序号、epoch、队列容量和 USB 就绪状态。重复的最后一包不会重复入队，也不延长租期；乱序、过期或归属于其他会话的数据包会被拒绝。

## 时序与释放

每个状态得到 USB 发送完成确认后，至少保持其声明时长，再执行下一个状态。客户端最多保存 64 个待发送状态；板子最多保存 16 个状态，按队列容量进行流控。

后台每 100 ms 续租一次。约 600 ms 内没有有效数据包时，固件停止实时输入、清空队列、回中并递增 epoch，拒绝迟到的旧请求。超过 500 ms 的排队状态会触发停止，避免恢复网络后补放积压操作。

客户端在超时、无效确认或队列异常时停止发送，不自动重试不确定的输入；恢复前先查询状态，且不接管其他仍有效的会话。USB 挂起或断开时，释放报告等待主机恢复通信后发送。
