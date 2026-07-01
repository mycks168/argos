#!/usr/bin/env bash
set -euo pipefail

SESSION="${ARGOS_TERMINAL_TMUX_SESSION:-argos-terminal}"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <command>" >&2
  exit 2
fi

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux new-session -d -s "$SESSION" -c /home/yuki/argos
fi

tmux send-keys -t "$SESSION" "$*" Enter
