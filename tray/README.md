# pim 托盘客户端

系统托盘常驻的 IM 消息提醒客户端（Windows / macOS），连接 pim 服务端的消息推送，
无需开着浏览器页面即可收到新消息的系统通知。

## 功能

- 实时消息 → 系统原生通知（Windows Toast / macOS 通知中心）
- 托盘图标：绿=已连接、红=断线自动重连、灰=提醒已暂停；右上角红色数字为未读数
- 历史消息（backlog）只累计未读不弹窗，与网页端行为一致
- 菜单：打开网页桌面（双击托盘图标）、暂停/恢复提醒、清除未读、退出

## 使用

```bash
pip install -r requirements.txt
python pim_tray.py
```

首次运行会在脚本目录生成 `config.json`，按需修改：

```json
{
  "server": "https://192.168.20.19:3001",
  "verify_tls": false
}
```

- `server`：pim 服务端统一入口地址（Caddy 的 3001 端口）
- `verify_tls`：自签名证书保持 `false`；如果换成正式证书改为 `true`

无界面连通性测试（只打印事件流，不显示托盘）：

```bash
python pim_tray.py --test-once
```

## 开机自启（可选）

- **Windows**：`Win+R` 运行 `shell:startup`，把 `pim_tray.py` 的快捷方式放进去
  （目标形如 `pythonw.exe D:\path\to\pim_tray.py`，用 pythonw 不弹控制台窗口）
- **macOS**：系统设置 → 通用 → 登录项，添加一个运行本脚本的 Automator 应用或 shell 包装
