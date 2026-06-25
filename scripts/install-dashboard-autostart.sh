#!/usr/bin/env bash
set -euo pipefail

# ユーザーsystemdでHDMIダッシュボードを自動表示する。
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
project_dir="${ARGOS_PROJECT_DIR:-${source_dir}}"
unit_dir="${HOME}/.config/systemd/user"
unit_path="${unit_dir}/argos-dashboard-kiosk.service"
policy_dir="/etc/chromium/policies/managed"
mkdir -p "${unit_dir}"
sed "s|@PROJECT_DIR@|${project_dir}|g" \
  "${source_dir}/systemd/argos-dashboard-kiosk.service" \
  > "${unit_path}"
rm -f "${HOME}/.config/autostart/argos-dashboard.desktop"
rm -f "${HOME}/.config/autostart/squeekboard.desktop"
sudo install -d -m 755 "${policy_dir}"
sudo install -m 644 "${source_dir}/chromium/argos-dashboard.json" "${policy_dir}/argos-dashboard.json"
systemctl --user daemon-reload
systemctl --user enable --now argos-dashboard-kiosk.service
echo "installed: ${unit_path}"
