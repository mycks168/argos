#!/usr/bin/env bash
set -euo pipefail

# ウェイクワードのバージイン（読み上げ中の割り込み）に必要な音響エコーキャンセル(AEC)を、
# PipeWireの module-echo-cancel（WebRTC AEC3）で常設する。Raspberry Pi OS / Ubuntu 共通。
#
# ARGOSはTTSを aplay、ウェイクワード/STTを arecord で鳴らし、どちらもALSAを直接叩く
# （PipeWireを経由しない）。そのため PipeWire の仮想ノード ec-sink/ec-source をそのまま
# .env に書いても aplay/arecord は開けない。ALSA↔PipeWire を橋渡しする ALSA PCM
# （ec_sink / ec_source）を /etc/asound.conf に定義し、その **ALSA名** を .env に指定する。
# （~/.asoundrc は Raspberry Pi OS 等で再起動時に消えることがあるため、システム側に置く。sudo要）
#
# 構成:
#   1. PipeWire ドロップイン設定  … 既定入出力に追従する ec-source / ec-sink ノードを作る
#   2. ALSA ブリッジPCM (/etc/asound.conf) … aplay/arecord から使える ec_sink / ec_source を定義
#   3. WirePlumber ドロップイン    … アイドルサスペンド無効化 + HDMI headroom増（プチプチ防止）
#   4. 既定sink音量を100%に固定    … ec_sink経由でTTS音量がPipeWire既定sink音量に従うため
#   5. .env（手動）        … AUDIO_OUTPUT_DEVICE=ec_sink / AUDIO_INPUT_DEVICES=ec_source
#
# 使い方:
#   scripts/setup-echo-cancel.sh                 # 1〜4を設定（依存が足りなければ案内して中断）
#   scripts/setup-echo-cancel.sh --install-deps  # 依存をapt導入してから設定する
#   scripts/setup-echo-cancel.sh --revert        # 1〜3を撤去して元に戻す（音量は案内のみ）
#   scripts/setup-echo-cancel.sh --help
#
# 注意: 実運用と同じ音量で自己エコーが閾値下まで消えることを必ず実機計測すること。
#       車載など環境が変わったら再計測が要る（docs/basic_design.md参照）。

conf_dir="${PIPEWIRE_CONF_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/pipewire/pipewire.conf.d}"
conf_path="${conf_dir}/99-argos-echo-cancel.conf"
wp_conf_dir="${WIREPLUMBER_CONF_DIR:-${XDG_CONFIG_HOME:-${HOME}/.config}/wireplumber/wireplumber.conf.d}"
wp_conf_path="${wp_conf_dir}/51-argos-echo-cancel.conf"
# 旧バージョンが置いた可能性のあるファイル名（移行のため撤去対象に含める）。
wp_conf_legacy="${wp_conf_dir}/51-argos-no-suspend.conf"
# ALSAブリッジPCMの置き場所。~/.asoundrc は環境により再起動で消えるためシステム側に置く。
asound_path="${ASOUND_CONF_PATH:-/etc/asound.conf}"
# システムファイルへの書き込み用。テスト時は SETUP_SUDO="" で無効化できる。
sudo_cmd="${SETUP_SUDO-sudo}"
marker_begin="# >>> argos-echo-cancel >>>"
marker_end="# <<< argos-echo-cancel <<<"
# HDMIのDMA枯渇(xrun)によるプチプチを防ぐための出力headroom（サンプル数）。Pi 5 の実測値。
alsa_headroom="${ALSA_HEADROOM:-8192}"

usage() {
  cat <<USAGE
usage: $(basename "$0") [--install-deps | --revert | --help]
  (引数なし)      エコーキャンセルの PipeWire設定 と ALSAブリッジ を設定する
  --install-deps  不足パッケージを apt で導入してから設定する
  --revert        設定を削除して元に戻す（既定デバイスや .env には触れない）
USAGE
}

# --- 依存検出 -----------------------------------------------------------------

# ALSA↔PipeWire ブリッジ（type pipewire プラグイン）のsoが存在するか。
# find|grep は pipefail 下で grep 早期終了→find が SIGPIPE で死に誤判定するため、
# パイプを使わず -print -quit の出力有無で判定する。
has_pipewire_alsa() {
  [ -n "$(find /usr/lib /usr/lib64 -name "libasound_module_pcm_pipewire.so" -print -quit 2>/dev/null)" ]
}

# WebRTC AEC3 の SPA プラグインが存在するか。
has_aec_webrtc() {
  [ -n "$(find /usr/lib /usr/lib64 -name "libspa-aec-webrtc.so" -print -quit 2>/dev/null)" ]
}

# PipeWire が稼働しているか。
pipewire_running() {
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" pw-cli info 0 >/dev/null 2>&1
}

check_deps() {
  local missing=0
  if ! has_pipewire_alsa; then
    echo "  [不足] pipewire-alsa（ALSA↔PipeWireブリッジ）"
    missing=1
  else
    echo "  [OK]   pipewire-alsa"
  fi
  if ! has_aec_webrtc; then
    echo "  [不足] libspa-aec-webrtc（WebRTC AEC3）"
    missing=1
  else
    echo "  [OK]   libspa-aec-webrtc"
  fi
  if ! pipewire_running; then
    echo "  [警告] PipeWireが稼働していない（設定後に systemctl --user restart pipewire が要る）"
  else
    echo "  [OK]   PipeWire稼働中"
  fi
  return "${missing}"
}

install_deps() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "apt が見つからない。Debian/Ubuntu系以外は手動で pipewire-alsa と" >&2
    echo "libspa-aec-webrtc 相当（libspa-0.2-modules）を導入すること。" >&2
    exit 1
  fi
  # Raspberry Pi OS も Ubuntu も同じパッケージ名で足りる。
  echo "apt でエコーキャンセルの依存を導入する..."
  sudo apt-get update
  sudo apt-get install -y pipewire-alsa libspa-0.2-modules
}

# --- インストール / 撤去 -------------------------------------------------------

write_pipewire_conf() {
  mkdir -p "${conf_dir}"
  # target.object を指定しないことで既定の入出力に追従させ、デバイス名のハードコードを避ける。
  cat > "${conf_path}" <<'EOF'
# ARGOSバージイン用のエコーキャンセル。既定の入出力デバイスに追従する。
context.modules = [
  { name = libpipewire-module-echo-cancel
    args = {
      library.name = aec/libspa-aec-webrtc
      aec.args = {
        webrtc.gain_control = true
        webrtc.extended_filter = true
      }
      capture.props   = { node.passive = true }
      playback.props  = { node.passive = true }
      source.props    = { node.name = ec-source  node.description = "ARGOS Echo-Cancel Source" }
      sink.props      = { node.name = ec-sink    node.description = "ARGOS Echo-Cancel Sink" }
    }
  }
]
EOF
  echo "installed: ${conf_path}"
}

# /etc/asound.conf のマーカー区間を除去する（既存の他設定は残す）。
strip_asound_block() {
  ${sudo_cmd} test -f "${asound_path}" || return 0
  ${sudo_cmd} sed -i "/${marker_begin}/,/${marker_end}/d" "${asound_path}"
  # マーカー区間だけで中身が空になったらファイルごと削除（他設定があれば残す）。
  if [ -z "$(${sudo_cmd} cat "${asound_path}" 2>/dev/null | tr -d '[:space:]')" ]; then
    ${sudo_cmd} rm -f "${asound_path}"
  fi
}

write_asound_bridge() {
  # 既存のマーカー区間があれば一旦消してから追記（冪等）。default は変えず ec_ 名だけ足す。
  # ~/.asoundrc は環境により再起動で消えるため、システムの /etc/asound.conf に置く（sudo）。
  strip_asound_block
  ${sudo_cmd} tee -a "${asound_path}" >/dev/null <<EOF
${marker_begin}
# ARGOSのaplay/arecordからPipeWireのEC済みノードへ橋渡しするALSA PCM。default は変更しない。
pcm.ec_sink {
    type pipewire
    playback_node "ec-sink"
    hint { show on description "ARGOS EC sink" }
}
pcm.ec_source {
    type pipewire
    capture_node "ec-source"
    hint { show on description "ARGOS EC source" }
}
${marker_end}
EOF
  echo "updated:   ${asound_path}（ec_sink / ec_source を定義）"
}

write_wireplumber_conf() {
  mkdir -p "${wp_conf_dir}"
  # 旧名があれば除去して新名に統一。
  rm -f "${wp_conf_legacy}"
  cat > "${wp_conf_path}" <<EOF
# ARGOSバージイン用の音切れ対策。
# (1) アイドルサスペンド無効化: 無音時にHDMIが suspend↔再開 を繰り返すプチプチを防ぐ。
# (2) HDMI headroom増: Pi 5 のHDMI+エコーキャンセルは headroom が小さいと再生が
#     間に合わず xrun でプチプチ鳴るため、DMAバッファに余裕を持たせる。
monitor.alsa.rules = [
  {
    matches = [
      { node.name = "~alsa_output.*" }
    ]
    actions = {
      update-props = {
        session.suspend-timeout-seconds = 0
        api.alsa.headroom = ${alsa_headroom}
      }
    }
  }
  {
    matches = [
      { node.name = "~alsa_input.*" }
    ]
    actions = {
      update-props = {
        session.suspend-timeout-seconds = 0
      }
    }
  }
]
EOF
  echo "installed: ${wp_conf_path}"
}

set_default_sink_volume() {
  # ec_sink経由にするとTTS音量がPipeWireの既定sink音量に従うため100%に固定する
  # （音量調整はARGOS内部のvolume設定で行う前提）。WirePlumberが値を記憶し再起動後も保つ。
  if XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>/dev/null; then
    echo "volume:    既定sink=100%（ec_sink経由のTTS音量用）"
  else
    echo "warn:      既定sink音量の設定に失敗（wpctl不可。反映後に手動で 'wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0'）"
  fi
}

do_install() {
  echo "=== 依存チェック ==="
  if ! check_deps; then
    echo
    echo "依存が不足している。'$(basename "$0") --install-deps' で導入するか、手動で:" >&2
    echo "  sudo apt-get install -y pipewire-alsa libspa-0.2-modules" >&2
    exit 1
  fi
  echo
  write_pipewire_conf
  write_asound_bridge
  write_wireplumber_conf
  set_default_sink_volume
  echo
  echo "次の手順:"
  echo "  1) systemctl --user restart pipewire pipewire-pulse wireplumber   # 設定を反映"
  echo "       ※ 'Failed to connect to bus: No medium found' が出たら、先に次を実行:"
  echo "         export XDG_RUNTIME_DIR=/run/user/\$(id -u)"
  echo "  2) 疎通確認（音が出ることを先に確かめる。無音のまま本番投入しない）:"
  echo "       aplay -D ec_sink /usr/share/sounds/alsa/Front_Center.wav"
  echo "       arecord -D ec_source -d 2 -f S16_LE -r 16000 /tmp/ec_test.wav && aplay /tmp/ec_test.wav"
  echo "  3) .env に以下を設定して ARGOS を再起動:"
  echo "       AUDIO_OUTPUT_DEVICE=ec_sink"
  echo "       AUDIO_INPUT_DEVICES=ec_source"
  echo "       ARGOS_WAKEWORD_BARGEIN_ENABLED=true"
  echo "  4) 実運用音量で自己エコーが消えるか実機計測（docs/basic_design.md）"
  echo
  echo "元に戻すには: $(basename "$0") --revert"
}

do_revert() {
  if [ -f "${conf_path}" ]; then
    rm -f "${conf_path}"
    echo "removed:   ${conf_path}"
  else
    echo "not found: ${conf_path}（PipeWire設定はすでに無い）"
  fi
  if ${sudo_cmd} test -f "${asound_path}" && ${sudo_cmd} grep -q "${marker_begin}" "${asound_path}"; then
    strip_asound_block
    echo "cleaned:   ${asound_path}（ec_sink / ec_source を除去）"
  else
    echo "not found: ${asound_path} の ARGOSブロック（すでに無い）"
  fi
  if [ -f "${wp_conf_path}" ] || [ -f "${wp_conf_legacy}" ]; then
    rm -f "${wp_conf_path}" "${wp_conf_legacy}"
    echo "removed:   ${wp_conf_path}（サスペンド無効化 / headroom を撤去）"
  else
    echo "not found: ${wp_conf_path}（WirePlumber設定はすでに無い）"
  fi
  echo
  echo "反映するには: systemctl --user restart pipewire pipewire-pulse wireplumber"
  echo "  ※ 'Failed to connect to bus: No medium found' が出たら 'export XDG_RUNTIME_DIR=/run/user/\$(id -u)' を先に実行"
  echo ".env で AUDIO_OUTPUT_DEVICE / AUDIO_INPUT_DEVICES / ARGOS_WAKEWORD_BARGEIN_ENABLED を"
  echo "変更していた場合は、そちらも元に戻して ARGOS を再起動すること。"
  echo "既定sink音量(100%)は元に戻していない。必要なら 'wpctl set-volume @DEFAULT_AUDIO_SINK@ <値>' で調整すること。"
}

# --- 引数処理 -----------------------------------------------------------------

case "${1:-}" in
  "")
    do_install
    ;;
  --install-deps)
    install_deps
    echo
    do_install
    ;;
  --revert)
    do_revert
    ;;
  -h | --help)
    usage
    ;;
  *)
    echo "unknown option: $1" >&2
    usage >&2
    exit 1
    ;;
esac
