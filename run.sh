#!/usr/bin/env bash
# Supervised start: keeps the CPU awake and restarts the server if it ever crashes.
cd "$(dirname "$0")"
command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
export MAHLA_HOST="${MAHLA_HOST:-127.0.0.1}"
[ -f .env ] && set -a && . ./.env && set +a
while true; do
  python -m mahlabahlasele
  echo "[run.sh] server exited ($?), restarting in 5s (Ctrl+C to quit)"
  sleep 5
done
