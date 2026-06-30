#!/usr/bin/env bash
# 直接用 tmux 启动 CLI runtime，并投递一句项目总结请求。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${AGENTRUN_TMUX_PROFILE:-tmux-codex}"
SESSION="${AGENTRUN_TMUX_SESSION:-agent-project-summary}"
WINDOW="${AGENTRUN_TMUX_WINDOW:-summary-$(date +%Y%m%d-%H%M%S)}"
PROMPT="看下当前项目实现了什么"
COMMAND="${AGENTRUN_TMUX_COMMAND:-}"
ATTACH=1
DRY_RUN=0

usage() {
  cat <<'EOF'
用法：
  scripts/tmux_project_summary.sh
  scripts/tmux_project_summary.sh "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --profile tmux-claude "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --no-attach
  scripts/tmux_project_summary.sh --dry-run

默认行为：
  1. 启动 tmux session/window
  2. 在窗口里运行 codex
  3. 输入“看下当前项目实现了什么”
  4. attach 到当前终端，让你直接看到 tmux 会话

环境变量：
  AGENTRUN_TMUX_PROFILE   默认 tmux-codex；tmux-claude 会运行 claude
  AGENTRUN_TMUX_COMMAND   覆盖实际命令，例如 codex 或 claude
  AGENTRUN_TMUX_SESSION   默认 agent-project-summary
  AGENTRUN_TMUX_WINDOW    默认 summary-<timestamp>
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --no-attach)
      ATTACH=0
      shift
      ;;
    --attach)
      ATTACH=1
      shift
      ;;
    --profile)
      PROFILE="${2:?--profile 需要 profile id，例如 tmux-codex}"
      shift 2
      ;;
    --command)
      COMMAND="${2:?--command 需要可执行命令，例如 codex}"
      shift 2
      ;;
    --session)
      SESSION="${2:?--session 需要 tmux session 名}"
      shift 2
      ;;
    --window)
      WINDOW="${2:?--window 需要 tmux window 名}"
      shift 2
      ;;
    --)
      shift
      PROMPT="$*"
      break
      ;;
    -*)
      echo "未知参数：$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      PROMPT="$*"
      break
      ;;
  esac
done

if [ -z "$COMMAND" ]; then
  case "$PROFILE" in
    tmux-claude) COMMAND="claude" ;;
    tmux-codex|*) COMMAND="codex" ;;
  esac
fi

echo "tmux_session: $SESSION"
echo "tmux_window: $WINDOW"
echo "profile: $PROFILE"
echo "command: $COMMAND"
echo "cwd: $ROOT_DIR"
echo "prompt: $PROMPT"

if [ "$DRY_RUN" -eq 1 ]; then
  cat <<EOF

dry-run，不会启动 tmux。
实际执行会运行：
  tmux new-session/new-window -d -s "$SESSION" -n "$WINDOW" -c "$ROOT_DIR" "$COMMAND"
  tmux paste-buffer ... "$PROMPT"
  tmux attach -t "$SESSION"
EOF
  exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux 不可用，请先安装 tmux。" >&2
  exit 1
fi

if ! command -v "$COMMAND" >/dev/null 2>&1; then
  echo "命令不可用：$COMMAND" >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  pane_id="$(tmux new-window -d -t "$SESSION" -n "$WINDOW" -P -F '#{pane_id}' -c "$ROOT_DIR" "$COMMAND")"
else
  pane_id="$(tmux new-session -d -s "$SESSION" -n "$WINDOW" -P -F '#{pane_id}' -c "$ROOT_DIR" "$COMMAND")"
fi

if [ -z "$pane_id" ]; then
  echo "tmux 未返回 pane_id，启动失败。" >&2
  exit 1
fi

deadline=$((SECONDS + 20))
while [ "$SECONDS" -lt "$deadline" ]; do
  if tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -Fxq "$pane_id"; then
    break
  fi
  sleep 0.2
done

if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -Fxq "$pane_id"; then
  echo "tmux pane 已退出，无法投递 prompt：$pane_id" >&2
  exit 1
fi

# 给 TUI 一点初始化时间；如果还在加载，paste 也会进入当前输入缓冲。
sleep 1
buffer="project-summary-$$"
printf '%s' "$PROMPT" | tmux load-buffer -b "$buffer" -
tmux paste-buffer -d -b "$buffer" -t "$pane_id"
tmux send-keys -t "$pane_id" C-m

cat <<EOF

已投递到 tmux。
pane_id: $pane_id

查看窗口：
  tmux attach -t "$SESSION"

停止会话：
  tmux kill-session -t "$SESSION"
EOF

if [ "$ATTACH" -eq 1 ]; then
  if [ -n "${TMUX:-}" ]; then
    tmux switch-client -t "$SESSION:$WINDOW"
  else
    tmux attach -t "$SESSION"
  fi
fi
