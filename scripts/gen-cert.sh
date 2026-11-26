#!/bin/bash
# 生成自签名证书（供 Caddy 统一入口用）
# 证书的 SAN 需要包含实际访问用的 IP/域名，否则浏览器握手失败。
# 用法: ./gen-cert.sh [IP或域名] ...   默认取本机所有 IPv4 地址
set -e

DIR="$(cd "$(dirname "$0")/.." && pwd)/certs"
mkdir -p "$DIR"

# 收集本机 IPv4 + localhost
HOSTS="$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' || true)"
HOSTS="$HOSTS 127.0.0.1 localhost"
[ "$#" -gt 0 ] && HOSTS="$HOSTS $*"

SAN=""
for h in $HOSTS; do
    if [[ "$h" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        SAN="$SAN,IP:$h"
    else
        SAN="$SAN,DNS:$h"
    fi
done
SAN="${SAN#,}"
echo "SAN: $SAN"

openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$DIR/key.pem" -out "$DIR/cert.pem" \
    -subj "/CN=pim.local" -addext "subjectAltName=$SAN"

chmod 600 "$DIR/key.pem"
echo "已生成: $DIR/cert.pem / $DIR/key.pem"
echo "如服务已在运行: docker compose restart caddy"
