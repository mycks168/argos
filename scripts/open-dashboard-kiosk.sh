#!/usr/bin/env bash
set -euo pipefail

# HDMI画面へARGOSダッシュボードを全画面表示する。
if pgrep -f "[c]hromium.*--kiosk.*http://127.0.0.1:${ARGOS_DASHBOARD_PORT:-8765}/" >/dev/null; then
  exit 0
fi

exec chromium \
  --user-data-dir="${HOME}/.config/argos-dashboard-chromium-kiosk" \
  --password-store=basic \
  --lang=ja \
  --disable-extensions \
  --disable-features=Translate,TranslateUI \
  --disable-translate \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --autoplay-policy=no-user-gesture-required \
  "http://127.0.0.1:${ARGOS_DASHBOARD_PORT:-8765}/"
