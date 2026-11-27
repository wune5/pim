# pim-tray (Go 版托盘客户端)

系统托盘常驻接收 pim 容器的消息推送。单文件静态编译，**免装 Python/依赖，双击即用**，约 8~10MB。

支持 **Windows / macOS / Linux**（Linux 需要 `libayatana-appindicator` 或支持 StatusNotifier 的桌面环境，主流 GNOME/KDE 均可）。

## 功能

- 实时消息 → 系统原生通知（Windows Toast / macOS 通知中心 / Linux notify-send）
- 托盘图标状态：绿=在线、**暗灰=断开**、浅灰=暂停、**橙↔绿闪烁=有未读**（QQ 式，点开即停）
- 右上角红色角标显示未读数
- **悬浮图标显示未读消息摘要**（状态 + 未读数 + 最近 3 条）
- backlog 历史消息只累计未读不弹窗；菜单可暂停提醒、清除未读
- 连接断开自动指数退避重连；诊断日志写同目录 `tray.log`

## 使用

### 方式一：下载编译好的（推荐）

到 [Releases](https://github.com/wune5/pim/releases) 下载对应平台产物：

| 文件 | 平台 |
|---|---|
| `pim-tray-windows-amd64.exe` | Windows 64 位 |
| `pim-tray-windows-arm64.exe` | Windows ARM |
| `pim-tray-darwin-universal` | macOS (Intel + Apple Silicon) |
| `pim-tray-linux-amd64` / `-arm64` | Linux |

首次运行会在同目录生成 `config.json`，按需修改：

```json
{"server": "https://192.168.20.19:3001", "verify_tls": false}
```

macOS 首次运行需 `chmod +x pim-tray-darwin-universal`，并在系统设置中允许运行；开机自启可放入「登录项」。

### 方式二：自己编译

```bash
cd tray-go
go build -ldflags "-s -w" -o pim-tray .
```

macOS 构建需要 Xcode Command Line Tools（cgo）；Windows / Linux 均为纯 Go 无 cgo。

## 与 Python 版的关系

`../tray/` 是 Python 参考实现（功能一致），Go 版为发布交付物；两者可独立使用，`config.json` 格式通用。
