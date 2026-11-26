# im-desktop

把 QQ / 微信 / 钉钉 三个官方 Linux 客户端装进一个 webtop 容器，浏览器远程操作，
并用桌面通知中继把新消息实时推送到浏览器页面（或手机 ntfy）。多架构镜像
（**linux/amd64 + linux/arm64**）由 GitHub Actions 自动构建并发布到 ghcr.io。

```
QQ/微信/钉钉 ──桌面通知──> DBus ──dbus-monitor──> notify_relay.py
                                        ├─ SSE ──> 浏览器 http://IP:8787
                                        └─ 可选 ─> ntfy ──> 手机 App
```

## 快速开始

```bash
mkdir im-desktop && cd im-desktop
# 保存 docker-compose.yml 后：
docker compose up -d
```

- 浏览器桌面：`http://IP:3000`（HTTP）或 `https://IP:3001`（HTTPS，**贴图/文件传输必须走这个**）
- 消息推送页：`http://IP:8787`（页面开着实时刷新；点「开启浏览器通知」可弹系统通知）
- 手机兜底：容器环境变量加 `NTFY_URL=https://ntfy.sh/你的私密topic`，ntfy App 订阅同 topic

首次启动后在桌面里打开 QQ/微信/钉钉扫码登录，数据落盘在 `./data`，重启不丢。

## 客户端能力

| 能力 | 支持情况 |
|---|---|
| 浏览器复制文本进容器 | ✅ 双向 |
| 浏览器贴图（如截图进微信） | ✅ 双向（需 HTTPS :3001） |
| 浏览器拖拽/下载文件 | ✅（侧边栏文件管理器） |
| 收到消息浏览器推送 | ✅（:8787，依赖客户端开启通知） |
| 回复消息 | 在浏览器桌面里直接操作客户端 |

## 镜像构建

`docker build` 时通过 build-args 传入三个客户端 deb 的下载地址
（按 `TARGETARCH` 自动选 amd64/arm64 包）：

```bash
docker build -t im-desktop . \
  --build-arg WECHAT_URL_AMD64=... --build-arg WECHAT_URL_ARM64=... \
  --build-arg QQ_URL_AMD64=... --build-arg QQ_URL_ARM64=... \
  --build-arg DINGTALK_URL_AMD64=... --build-arg DINGTALK_URL_ARM64=...
```

不传时使用 Dockerfile 里的默认版本。`scripts/resolve-urls.sh` 可独立运行，
打印当前解析出的最新地址（本地跑会直接输出结果）。

### GitHub Actions 自动构建

`.github/workflows/build.yml`：
1. `resolve-urls` job 运行解析脚本，自动跟踪三个客户端的最新版本；
2. `build` job 在 **amd64（ubuntu-latest）和 arm64（ubuntu-24.04-arm）原生 runner**
   上并行构建并按 digest 推送（arm 原生 runner 对公开仓库免费，比 QEMU 快得多）；
3. `merge` job 把两个 digest 合并成多架构 manifest，打 `latest` 和 `sha-xxxxxxx` 标签。

产物：`ghcr.io/<owner>/<repo>:latest`（多架构 manifest，docker pull 自动匹配架构）。

注意：私有仓库没有免费 arm runner，把 matrix 里的 `ubuntu-24.04-arm` 换回
`ubuntu-latest` 并让 buildx 走 QEMU 即可（构建时间会显著变长）。

## 已知限制

- 消息推送是**只读**的：仅收到通知里的文本摘要，回复需在浏览器桌面里操作客户端。
- 长消息会被客户端通知截断；图片/语音/文件只显示提示文字。
- 窗口聚焦时部分应用可能不发通知。
- QQ/微信/钉钉均为官方客户端本体，中继只旁听系统通知，不碰协议，无封号风险。

## 三个客户端的官方下载源

| 客户端 | 地址 | arm64 |
|---|---|---|
| QQ | https://im.qq.com/linuxqq/index.html | ✅ |
| 微信 | https://linux.weixin.qq.com/ | ✅（选「ARM 版」） |
| 钉钉 | https://www.dingtalk.com/ 下载页 | ✅ |
