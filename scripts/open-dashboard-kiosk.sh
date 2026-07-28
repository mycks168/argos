#!/usr/bin/env bash
set -euo pipefail

# HDMI画面へARGOSダッシュボードを全画面表示する。
export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin:${PATH:-}"
# snap版Chromiumにはホストの日本語フォントが公開されないため、リビジョン別の
# ユーザーフォント領域へ同期する。current symlinkによりsnap更新後も追従できる。
CHROMIUM_SNAP_FONT_DIR="${HOME}/snap/chromium/current/.local/share/fonts/argos"
if [ -d "${HOME}/snap/chromium/current" ]; then
  install -d -m 755 "${CHROMIUM_SNAP_FONT_DIR}"
  for font in /usr/share/fonts/opentype/ipafont-gothic/*.ttf /usr/share/fonts/opentype/ipafont-mincho/*.ttf; do
    [ -f "${font}" ] || continue
    install -m 644 "${font}" "${CHROMIUM_SNAP_FONT_DIR}/"
  done
fi

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

if pgrep -f "[c]hromium.*argos-dashboard-chromium-kiosk" >/dev/null; then
  exit 0
fi

# .envからconfig.yamlへ移行済みの環境でも、キオスクに必要な値を取得する。
if [ -x "${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}/../.venv/bin/python" ]; then
  SCRIPT_DIR="${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
  mapfile -t DASHBOARD_CONFIG < <(
    "${SCRIPT_DIR}/../.venv/bin/python" -c \
      'from pathlib import Path; from argos.yaml_config import load_yaml_environment; v=load_yaml_environment(Path("'"${SCRIPT_DIR}"'").parent / "config.yaml"); print(v.get("ARGOS_DASHBOARD_PORT", "")); print(v.get("ARGOS_DASHBOARD_VIEW_KEY", ""))'
  )
  ARGOS_DASHBOARD_PORT="${ARGOS_DASHBOARD_PORT:-${DASHBOARD_CONFIG[0]:-}}"
  ARGOS_DASHBOARD_VIEW_KEY="${ARGOS_DASHBOARD_VIEW_KEY:-${DASHBOARD_CONFIG[1]:-}}"
fi

# 閲覧キーが設定されていれば、初回アクセスでCookieを受け取るためURLへ付与する。
DASHBOARD_URL="http://127.0.0.1:${ARGOS_DASHBOARD_PORT:-8765}/"
if [ -n "${ARGOS_DASHBOARD_VIEW_KEY:-}" ]; then
  DASHBOARD_URL="${DASHBOARD_URL}?key=${ARGOS_DASHBOARD_VIEW_KEY}"
fi

# 母艦起動直後はダッシュボードより先にChromiumが立ち上がるため、
# 疎通が取れるまでローカルの接続待ち画面を表示し、取れたら自動遷移する。
# snap版Chromiumは/opt配下のfile:// URLを読めないため、snapがアクセスできる
# ユーザー共通領域へ待機画面をコピーする。deb版Chromiumでも同じ配置を利用できる。
SCRIPT_DIR="${SCRIPT_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)}"
SPLASH_DIR="${HOME}/snap/chromium/common/argos-dashboard-kiosk"
SPLASH_FILE="${SPLASH_DIR}/kiosk-splash.html"
install -d -m 700 "${SPLASH_DIR}"
install -m 600 "${SCRIPT_DIR}/kiosk-splash.html" "${SPLASH_FILE}"
SPLASH_FILE_URL="$(python3 -c 'import pathlib, sys; print(pathlib.Path(sys.argv[1]).resolve().as_uri())' \
  "${SPLASH_FILE}")"
ENCODED_TARGET="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' \
  "${DASHBOARD_URL}")"
# フラグメントはローカルファイル名の解決に使われないため、認証キーを安全に渡せる。
SPLASH_URL="${SPLASH_FILE_URL}#target=${ENCODED_TARGET}"

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
  "${SPLASH_URL}"
