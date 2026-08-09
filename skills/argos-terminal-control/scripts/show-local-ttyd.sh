#!/usr/bin/env bash
set -euo pipefail

# 3番以上のファイル記述子をすべてクローズして、親プロセスのパイプ引き継ぎを防ぐ
for fd in $(ls /proc/self/fd 2>/dev/null || true); do
  if [[ "$fd" =~ ^[0-9]+$ ]] && (( fd > 2 )); then
    eval "exec $fd>&-"
  fi
done

ARGOS_DIR="${ARGOS_DIR:-$HOME/argos}"
SESSION="${ARGOS_TERMINAL_TMUX_SESSION:-argos-terminal}"
TTYD_BIN="${TTYD_BIN:-/usr/local/bin/ttyd}"
TTYD_HOST="${TTYD_HOST:-127.0.0.1}"
TTYD_PORT="${TTYD_PORT:-7681}"
DASHBOARD_URL="${ARGOS_DASHBOARD_URL:-http://127.0.0.1:8765}"
LOG_FILE="${ARGOS_TTYD_LOG:-/tmp/argos-ttyd-local-tmux.log}"

token_from_env_file() {
  awk -F= '
    $1 ~ /^[[:space:]]*ARGOS_DASHBOARD_TOKEN[[:space:]]*$/ {
      value=$0
      sub(/^[^=]*=/, "", value)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^["'\'']|["'\'']$/, "", value)
      print value
      exit
    }
  ' "$ARGOS_DIR/.env"
}

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -c "$ARGOS_DIR"
fi

if ! ss -ltnp | rg -q ":${TTYD_PORT}.*ttyd"; then
  systemctl --user stop argos-ttyd.service 2>/dev/null || true
  systemd-run --user --unit=argos-ttyd \
    "$TTYD_BIN" -W -i "$TTYD_HOST" -p "$TTYD_PORT" -t fontSize=18 \
    tmux attach-session -t "$SESSION"
  sleep 1
fi

TOKEN="${ARGOS_DASHBOARD_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(token_from_env_file)"
fi

if [[ -z "$TOKEN" ]]; then
  echo "ARGOS_DASHBOARD_TOKEN が見つかりません。" >&2
  exit 1
fi

URL="http://${TTYD_HOST}:${TTYD_PORT}/?local=$(date +%s)"
curl -sS -X POST "${DASHBOARD_URL%/}/api/events" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"overlay\",\"source\":\"codex\",\"target_slot\":\"center\",\"overlay_type\":\"terminal\",\"title\":\"local tmux\",\"url\":\"${URL}\",\"replace_top\":true}"
