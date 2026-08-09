---
name: argos-terminal-control
description: Control the ARGOS dashboard terminal backed by local ttyd and tmux. Use when the user asks to run commands in the visible ARGOS terminal pane, operate the ttyd+tmux overlay, recover the local terminal display, detach from a remote tmux/SSH session shown inside ARGOS, or keep terminal-result replies very concise because the dashboard already shows the output.
---

# ARGOS Terminal Control

## Core Rule

For terminal actions shown through the ARGOS dashboard, operate the local tmux session named `argos-terminal`. Keep the user-facing reply extremely short because the screen already shows command output.

Good replies:

- `実行したよ。`
- `戻ったよ。`
- `失敗。理由は: ...`

## Quick Commands

Send a command to the visible local tmux:

```bash
~/.codex/skills/argos-terminal-control/scripts/send-local-tmux.sh "ls"
```

Restore the dashboard center pane to local ttyd + tmux:

```bash
~/.codex/skills/argos-terminal-control/scripts/show-local-ttyd.sh
```

## Workflow

1. If the user asks to run a shell command, send it to `argos-terminal` with `scripts/send-local-tmux.sh`.
2. If the terminal pane is broken, blank, or connected to the wrong host, run `scripts/show-local-ttyd.sh`.
3. If the user asks to leave an inner remote tmux session, send `C-b d` to `argos-terminal`, then confirm the pane returns to the local shell prompt.
4. If the user asks to log out from a remote host, prefer detaching the inner tmux first, then allow the SSH command to close. Do not start a new direct `ttyd -> ssh -> <remote host>` connection unless explicitly requested.
5. After visible terminal operations, do not summarize output unless the user asks. One short completion sentence is enough.

## Local Details

- ARGOS repo: `~/argos`
- tmux session: `argos-terminal`
- ttyd bind: `127.0.0.1:7681`
- dashboard URL: `http://127.0.0.1:8765`
- dashboard event API: `POST /api/events` with `type: "overlay"`
- dashboard token source: `~/argos/.env`, key `ARGOS_DASHBOARD_TOKEN`

Never `source` `~/argos/.env`. Parse only the needed key because the file can contain shell-sensitive values.

## Verification

Use these checks when needed:

```bash
tmux capture-pane -t argos-terminal -p -S -40
pgrep -a ttyd || true
ss -ltnp | rg ':7681|ttyd' || true
```

For visual confirmation on the Raspberry Pi Wayland session:

```bash
XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim /tmp/argos-terminal-check.png
```
