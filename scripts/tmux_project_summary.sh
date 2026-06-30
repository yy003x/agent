#!/usr/bin/env bash
# 模拟 AgentRun:后台启动 tmux CLI runtime，投递 prompt，并在当前终端监控输出。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="${AGENTRUN_TMUX_PROFILE:-tmux-codex}"
SESSION="${AGENTRUN_TMUX_SESSION:-agent-project-summary}"
WINDOW="${AGENTRUN_TMUX_WINDOW:-summary-$(date +%Y%m%d-%H%M%S)}"
PROMPT="看下当前项目实现了什么"
COMMAND="${AGENTRUN_TMUX_COMMAND:-}"
MONITOR=1
MONITOR_SECONDS="${AGENTRUN_TMUX_MONITOR_SECONDS:-0}"
ATTACH=0
DRY_RUN=0

usage() {
  cat <<'EOF'
用法：
  scripts/tmux_project_summary.sh
  scripts/tmux_project_summary.sh "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --profile tmux-claude "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --no-monitor
  scripts/tmux_project_summary.sh --monitor-seconds 30
  scripts/tmux_project_summary.sh --attach
  scripts/tmux_project_summary.sh --dry-run

默认行为：
  1. 后台启动 tmux session/window
  2. 在窗口里运行 codex
  3. 输入“看下当前项目实现了什么”
  4. 当前脚本实时监控 tmux 输出；按 Ctrl-C 只停止监控，不杀 tmux

环境变量：
  AGENTRUN_TMUX_PROFILE          默认 tmux-codex；tmux-claude 会运行 claude
  AGENTRUN_TMUX_COMMAND          覆盖实际命令，例如 codex 或 claude
  AGENTRUN_TMUX_SESSION          默认 agent-project-summary；不能是纯数字
  AGENTRUN_TMUX_WINDOW           默认 summary-<timestamp>
  AGENTRUN_TMUX_MONITOR_SECONDS  默认 0，表示持续监控直到 Ctrl-C 或 pane 退出
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
    --no-monitor)
      MONITOR=0
      shift
      ;;
    --monitor)
      MONITOR=1
      shift
      ;;
    --monitor-seconds)
      MONITOR_SECONDS="${2:?--monitor-seconds 需要秒数}"
      shift 2
      ;;
    --attach)
      ATTACH=1
      MONITOR=0
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

validate_tmux_name() {
  local kind="$1"
  local value="$2"
  if [ -z "$value" ]; then
    echo "$kind 不能为空。" >&2
    exit 2
  fi
  if [[ "$value" == *:* ]]; then
    echo "$kind 不能包含冒号：$value" >&2
    exit 2
  fi
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "$kind 不能是纯数字：$value" >&2
    exit 2
  fi
}

pane_exists() {
  tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -Fxq "$1"
}

monitor_output() {
  local pane_id="$1"
  local log_file="$2"
  local started="$SECONDS"
  local tail_pid

  touch "$log_file"
  echo ""
  echo "开始监控 tmux 输出。按 Ctrl-C 只停止监控，tmux 会话继续在后台运行。"
  echo "log_file: $log_file"
  echo ""

  tail -n +1 -f "$log_file" &
  tail_pid="$!"
  trap 'kill "$tail_pid" 2>/dev/null || true; echo; echo "已停止监控，tmux 会话仍在后台。"; exit 130' INT TERM

  while pane_exists "$pane_id"; do
    if [ "$MONITOR_SECONDS" != "0" ] && [ $((SECONDS - started)) -ge "$MONITOR_SECONDS" ]; then
      break
    fi
    sleep 1
  done

  kill "$tail_pid" 2>/dev/null || true
  wait "$tail_pid" 2>/dev/null || true
  trap - INT TERM

  if pane_exists "$pane_id"; then
    echo ""
    echo "监控结束，tmux pane 仍在后台运行：$pane_id"
  else
    echo ""
    echo "tmux pane 已退出：$pane_id"
  fi
}

validate_tmux_name "tmux session 名" "$SESSION"
validate_tmux_name "tmux window 名" "$WINDOW"

RUN_DIR="$ROOT_DIR/runs/tmux-project-summary/${SESSION}-${WINDOW}"
LOG_FILE="$RUN_DIR/output.log"

echo "tmux_session: $SESSION"
echo "tmux_window: $WINDOW"
echo "profile: $PROFILE"
echo "command: $COMMAND"
echo "cwd: $ROOT_DIR"
echo "prompt: $PROMPT"
echo "monitor: $MONITOR"
echo "monitor_seconds: $MONITOR_SECONDS"

if [ "$DRY_RUN" -eq 1 ]; then
  cat <<EOF

dry-run，不会启动 tmux。
实际执行会运行：
  tmux new-session/new-window -d -s "$SESSION" -n "$WINDOW" -c "$ROOT_DIR" "$COMMAND"
  tmux pipe-pane ... "$LOG_FILE"
  tmux paste-buffer ... "$PROMPT"
  tail -f "$LOG_FILE"
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

mkdir -p "$RUN_DIR"
: >"$LOG_FILE"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  pane_id="$(tmux new-window -d -t "$SESSION" -n "$WINDOW" -P -F '#{pane_id}' -c "$ROOT_DIR" "$COMMAND")"
else
  pane_id="$(tmux new-session -d -s "$SESSION" -n "$WINDOW" -P -F '#{pane_id}' -c "$ROOT_DIR" "$COMMAND")"
fi

if [ -z "$pane_id" ]; then
  echo "tmux 未返回 pane_id，启动失败。" >&2
  exit 1
fi

tmux pipe-pane -o -t "$pane_id" "cat >> '$LOG_FILE'"

deadline=$((SECONDS + 20))
while [ "$SECONDS" -lt "$deadline" ]; do
  if pane_exists "$pane_id"; then
    break
  fi
  sleep 0.2
done

if ! pane_exists "$pane_id"; then
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

已投递到后台 tmux。
pane_id: $pane_id
log_file: $LOG_FILE

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
elif [ "$MONITOR" -eq 1 ]; then
  monitor_output "$pane_id" "$LOG_FILE"
fi
