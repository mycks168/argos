#!/usr/bin/env bash
set -euo pipefail

# HDMI画面へARGOSダッシュボードを全画面表示する。
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin:${PATH:-}"

if command -v xset >/dev/null 2>&1; then
  xset s off || true
  xset s noblank || true
  xset -dpms || true
fi

if command -v gsettings >/dev/null 2>&1; then
  gsettings set org.gnome.desktop.session idle-delay 0 || true
  gsettings set org.gnome.desktop.screensaver lock-enabled false || true
  gsettings set org.gnome.desktop.lockdown disable-lock-screen true || true
fi

if pgrep -f "[c]hromium.*--kiosk.*http://127.0.0.1:${ARGOS_DASHBOARD_PORT:-8765}/" >/dev/null; then
  exit 0
fi

# 閲覧キーが設定されていれば、初回アクセスでCookieを受け取るためURLへ付与する。
DASHBOARD_URL="http://127.0.0.1:${ARGOS_DASHBOARD_PORT:-8765}/"
if [ -n "${ARGOS_DASHBOARD_VIEW_KEY:-}" ]; then
  DASHBOARD_URL="${DASHBOARD_URL}?key=${ARGOS_DASHBOARD_VIEW_KEY}"
fi

exec chromium \
  --user-data-dir="${HOME}/.config/argos-dashboard-chromium-kiosk" \
  --password-store=basic \
  --lang=ja \
  --no-first-run \
  --no-default-browser-check \
  --disable-extensions \
  --disable-sync \
  --disable-background-networking \
  --disable-component-update \
  --disable-default-apps \
  --disable-search-engine-choice-screen \
  --disable-features=Translate,TranslateUI,SigninIntercept,ChromeWhatsNewUI,AutofillServerCommunication,MediaRouter \
  --disable-translate \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --autoplay-policy=no-user-gesture-required \
  "${DASHBOARD_URL}"
