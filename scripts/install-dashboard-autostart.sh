#!/usr/bin/env bash
set -euo pipefail

# デスクトップログイン時にHDMIダッシュボードを自動表示する。
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
autostart_dir="${HOME}/.config/autostart"
mkdir -p "${autostart_dir}"
sed "s|@PROJECT_DIR@|${project_dir}|g" \
  "${project_dir}/desktop/argos-dashboard.desktop" \
  > "${autostart_dir}/argos-dashboard.desktop"
echo "installed: ${autostart_dir}/argos-dashboard.desktop"
