#!/bin/bash
# Keep Cloudflare WARP in local SOCKS proxy mode. Never enable full tunnel.
set -euo pipefail

PORT="${WARP_PROXY_PORT:-41080}"

if ! command -v warp-cli >/dev/null 2>&1; then
  echo "warp-cli not installed" >&2
  exit 1
fi

systemctl start warp-svc
for _ in $(seq 1 30); do
  if warp-cli --accept-tos status >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! warp-cli --accept-tos registration show >/dev/null 2>&1; then
  warp-cli --accept-tos registration new
fi

warp-cli --accept-tos disconnect >/dev/null 2>&1 || true
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port "${PORT}"
warp-cli --accept-tos connect

for _ in $(seq 1 20); do
  if warp-cli --accept-tos status 2>/dev/null | grep -q "Connected"; then
    break
  fi
  sleep 1
done

settings="$(warp-cli --accept-tos settings)"
echo "${settings}"
if ! echo "${settings}" | grep -q "WarpProxy"; then
  echo "refusing to continue: WARP is not in proxy mode" >&2
  exit 2
fi

host_ip="$(curl -fsS --max-time 8 https://ipinfo.io/ip || true)"
echo "host_egress=${host_ip}"

curl -fsS --max-time 15 -x "socks5h://127.0.0.1:${PORT}" https://www.cloudflare.com/cdn-cgi/trace | grep -E '^(ip|warp|colo|loc)='
echo "warp proxy ready on 127.0.0.1:${PORT}"
