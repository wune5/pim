#!/usr/bin/env bash
# 解析三个 IM 客户端最新 deb 下载地址，输出 GitHub Actions outputs 格式。
#   微信:   官方固定地址（长期稳定）
#   QQ:     Flathub com.qq.QQ 清单（flathubbot 自动跟进官方最新版）
#   钉钉:   官方更新 JSON（gray + release 取新者），arm64 由 amd64 URL 推导并验证
# 任何一步失败都回退到内置默认地址。
set -uo pipefail

log() { echo "[resolve] $*"; }

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

WECHAT_AMD64="https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_x86_64.deb"
WECHAT_ARM64="https://dldir1v6.qq.com/weixin/Universal/Linux/WeChatLinux_arm64.deb"

QQ_AMD64_DEFAULT="https://qqdl.gtimg.cn/qqfile/QQNT/9.9.37/beta/816b3c79/linuxqq_3.2.35-54395_amd64.deb"
QQ_ARM64_DEFAULT="https://qqdl.gtimg.cn/qqfile/QQNT/9.9.37/beta/816b3c79/linuxqq_3.2.35-54395_arm64.deb"

DT_AMD64_DEFAULT="https://dtapp-pub.dingtalk.com/dingtalk-desktop/xc_dingtalk_update/linux_deb/Release/com.alibabainc.dingtalk_7.8.15.5092302_amd64.deb"
DT_ARM64_DEFAULT="https://dtapp-pub.dingtalk.com/dingtalk-desktop/xc_dingtalk_update/linux_deb/Release/com.alibabainc.dingtalk_7.8.15.5092302_arm64.deb"

verify() { curl -fsIL --max-time 30 -A "$UA" "$1" >/dev/null 2>&1; }

# ---------- QQ ----------
qq_resolve() {
  local payload amd arm
  payload=$(curl -fsSL --max-time 30 \
    "https://api.github.com/repos/flathub/com.qq.QQ/contents/com.qq.QQ.yaml") || return 1
  payload=$(printf '%s' "$payload" | python3 -c "
import sys, json, base64, re
c = base64.b64decode(json.load(sys.stdin)['content']).decode()
urls = re.findall(r'url: (\S+\.deb)', c)
amd = [u for u in urls if '_amd64.deb' in u]
arm = [u for u in urls if '_arm64.deb' in u]
print(amd[0] if amd else '')
print(arm[0] if arm else '')
") || return 1
  amd=$(printf '%s' "$payload" | awk 'NR==1')
  arm=$(printf '%s' "$payload" | awk 'NR==2')
  [ -z "$amd" ] || [ -z "$arm" ] && return 1
  verify "$amd" && verify "$arm" && { printf '%s\n%s\n' "$amd" "$arm"; return 0; }
  return 1
}

QQ_AMD64=""; QQ_ARM64=""
qq_out=$(qq_resolve || true)
if [ -n "$qq_out" ]; then
  QQ_AMD64=$(printf '%s' "$qq_out" | awk 'NR==1')
  QQ_ARM64=$(printf '%s' "$qq_out" | awk 'NR==2')
  log "QQ 解析成功: $QQ_AMD64"
else
  log "QQ 解析失败，使用默认地址"
  QQ_AMD64="$QQ_AMD64_DEFAULT"; QQ_ARM64="$QQ_ARM64_DEFAULT"
fi

# ---------- 钉钉 ----------
dt_resolve() {
  local base="https://dtapp-pub.dingtalk.com/dingtalk-desktop/xc_dingtalk_update/linux_deb/Update/other/amd64"
  local best="" best_ver="" j u v
  for j in linux_dingtalk_update_package_gray.json linux_dingtalk_update_package_release.json; do
    u=$(curl -fsSL --max-time 20 "$base/$j" 2>/dev/null \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('install',{}).get('package',{}).get('url',''))" 2>/dev/null) || continue
    [ -z "$u" ] && continue
    v=$(printf '%s' "$u" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    log "dingtalk 候选: $v $u"
    if [ -z "$best_ver" ] || [ "$(printf '%s\n%s\n' "$v" "$best_ver" | sort -V | tail -1)" = "$v" ]; then
      best="$u"; best_ver="$v"
    fi
  done
  [ -n "$best" ] && { printf '%s' "$best"; return 0; }
  return 1
}

DT_AMD64=""
dt_out=$(dt_resolve || true)
if [ -n "$dt_out" ]; then
  DT_AMD64="$dt_out"
  DT_ARM64="${DT_AMD64/_amd64.deb/_arm64.deb}"
  if verify "$DT_ARM64"; then
    log "钉钉解析成功: $DT_AMD64"
  else
    log "钉钉 arm64 验证失败，回退默认地址"
    DT_ARM64="$DT_ARM64_DEFAULT"
  fi
else
  log "钉钉解析失败，使用默认地址"
  DT_AMD64="$DT_AMD64_DEFAULT"; DT_ARM64="$DT_ARM64_DEFAULT"
fi

# ---------- 最终兜底校验 ----------
verify "$WECHAT_AMD64" || log "警告: 微信 amd64 地址校验失败"
verify "$WECHAT_ARM64" || log "警告: 微信 arm64 地址校验失败"
verify "$QQ_AMD64"     || { log "QQ amd64 校验失败，回退默认"; QQ_AMD64="$QQ_AMD64_DEFAULT"; }
verify "$QQ_ARM64"     || { log "QQ arm64 校验失败，回退默认"; QQ_ARM64="$QQ_ARM64_DEFAULT"; }
verify "$DT_AMD64"     || { log "钉钉 amd64 校验失败，回退默认"; DT_AMD64="$DT_AMD64_DEFAULT"; }
verify "$DT_ARM64"     || { log "钉钉 arm64 校验失败，回退默认"; DT_ARM64="$DT_ARM64_DEFAULT"; }

# ---------- 输出 ----------
out="wechat_amd64=$WECHAT_AMD64
wechat_arm64=$WECHAT_ARM64
qq_amd64=$QQ_AMD64
qq_arm64=$QQ_ARM64
dt_amd64=$DT_AMD64
dt_arm64=$DT_ARM64"

if [ -n "${GITHUB_OUTPUT:-}" ]; then
  printf '%s\n' "$out" >> "$GITHUB_OUTPUT"
else
  printf '%s\n' "$out"
fi
log "完成"
