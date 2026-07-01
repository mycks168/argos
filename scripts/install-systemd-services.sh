#!/usr/bin/env bash
set -euo pipefail

# ARGOS本体とAgent Runnerのsystemdユニットをインストールする。
project_dir="${ARGOS_PROJECT_DIR:-/opt/argos}"
service_user="${ARGOS_SERVICE_USER:-argos}"
service_group="${ARGOS_SERVICE_GROUP:-${service_user}}"
service_home="${ARGOS_SERVICE_HOME:-/home/argos}"
service_uid="${ARGOS_SERVICE_UID:-$(id -u "${service_user}" 2>/dev/null || echo 1000)}"
unit_dir="${ARGOS_SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
template_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/systemd"

render_unit() {
  local source_path="$1"
  local target_path="$2"
  sed \
    -e "s|@PROJECT_DIR@|${project_dir}|g" \
    -e "s|@ARGOS_USER@|${service_user}|g" \
    -e "s|@ARGOS_GROUP@|${service_group}|g" \
    -e "s|@USER_HOME@|${service_home}|g" \
    -e "s|@ARGOS_UID@|${service_uid}|g" \
    "${source_path}" | sudo tee "${target_path}" >/dev/null
}

sudo install -d -m 755 "${unit_dir}"
render_unit "${template_dir}/argos.service" "${unit_dir}/argos.service"
render_unit "${template_dir}/argos-agent-runner.service" "${unit_dir}/argos-agent-runner.service"
sudo systemctl daemon-reload

echo "installed: ${unit_dir}/argos.service"
echo "installed: ${unit_dir}/argos-agent-runner.service"
