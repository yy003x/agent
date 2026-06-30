#!/usr/bin/env bash
# AgentRun tmux smoke:通过 AgentRun 启动后台 tmux session、投递一句话并监控日志。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENTRUN_APP="$ROOT_DIR/apps/agentrun"
CONF_DIR="$ROOT_DIR/config/agentrun"
RUNS_DIR="$ROOT_DIR/runs/agentrun"
PROJECT="agent"
PROFILE="tmux-codex"
RUN_ID="session-project-summary-$(date +%Y%m%d-%H%M%S)-$$"
PROMPT="看下当前项目实现了什么"
MONITOR=1
MONITOR_SECONDS="${AGENTRUN_TMUX_MONITOR_SECONDS:-0}"
DRY_RUN=0

usage() {
  cat <<'EOF'
用法：
  scripts/tmux_project_summary.sh
  scripts/tmux_project_summary.sh "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --no-monitor "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --monitor-seconds 30 "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --dry-run

说明：
  这是 AgentRun tmux runtime 的测试脚本。脚本不直接配置 tmux 启动参数；
  tmux session/window 创建、命令启动、TUI 就绪等待、日志读取都通过 AgentRun 完成。
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
    --monitor-seconds)
      MONITOR_SECONDS="${2:?--monitor-seconds 需要秒数}"
      shift 2
      ;;
    --run-id)
      RUN_ID="${2:?--run-id 需要 run id}"
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

if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
elif [ -x "$ROOT_DIR/../.venv/bin/python3" ]; then
  PYTHON_BIN="$ROOT_DIR/../.venv/bin/python3"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

run_agentrun() {
  PYTHONPATH="$AGENTRUN_APP${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m agentrun.cli.main \
    --conf-dir "$CONF_DIR" \
    --runs-dir "$RUNS_DIR" \
    --json \
    "$@"
}

json_get() {
  local key="$1"
  local json_input
  json_input="$(cat)"
  JSON_INPUT="$json_input" "$PYTHON_BIN" - "$key" <<'PY'
import json
import os
import sys

data = json.loads(os.environ.get("JSON_INPUT") or "{}")
value = data
for part in sys.argv[1].split("."):
    if not part:
        continue
    if not isinstance(value, dict):
        value = ""
        break
    value = value.get(part, "")
if isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
elif value is None:
    print("")
else:
    print(value)
PY
}

json_ok() {
  local json_input
  json_input="$(cat)"
  JSON_INPUT="$json_input" "$PYTHON_BIN" - <<'PY'
import json
import os

data = json.loads(os.environ.get("JSON_INPUT") or "{}")
raise SystemExit(0 if data.get("ok") else 1)
PY
}

print_log_delta() {
  local previous="$1"
  local current="$2"
  PREVIOUS_LOG="$previous" CURRENT_LOG="$current" "$PYTHON_BIN" - <<'PY'
import os

previous = os.environ.get("PREVIOUS_LOG", "")
current = os.environ.get("CURRENT_LOG", "")
if current.startswith(previous):
    print(current[len(previous):], end="")
elif current != previous:
    print("\n--- log snapshot ---")
    print(current, end="" if current.endswith("\n") else "\n")
PY
}

validate_tmux_session_name() {
  local value="$1"
  if [ -z "$value" ]; then
    echo "AgentRun 返回的 tmux session 为空。" >&2
    exit 1
  fi
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "AgentRun 返回的 tmux session 不能是纯数字：$value" >&2
    exit 1
  fi
}

monitor_logs() {
  local run_id="$1"
  local pane_id="$2"
  local started="$SECONDS"
  local previous=""

  echo ""
  echo "开始通过 AgentRun 监控 tmux 日志。按 Ctrl-C 只停止监控，不杀 tmux。"
  echo ""

  trap 'echo; echo "已停止监控，tmux 会话仍在后台。"; exit 130' INT TERM
  while true; do
    status_json="$(run_agentrun session status "$run_id" --project "$PROJECT" || true)"
    classification="$(printf '%s' "$status_json" | json_get classification)"
    logs_json="$(run_agentrun session logs "$run_id" --project "$PROJECT" --tail 120 || true)"
    current="$(printf '%s' "$logs_json" | json_get content)"
    print_log_delta "$previous" "$current"
    previous="$current"

    if [ "$MONITOR_SECONDS" != "0" ] && [ $((SECONDS - started)) -ge "$MONITOR_SECONDS" ]; then
      echo ""
      echo "监控时间到，tmux pane 仍在后台：$pane_id"
      break
    fi
    if [ "$classification" = "orphaned" ] || [ "$classification" = "done" ] || [ "$classification" = "failed" ]; then
      echo ""
      echo "AgentRun 状态：$classification"
      break
    fi
    sleep 1
  done
  trap - INT TERM
}

echo "project: $PROJECT"
echo "profile: $PROFILE"
echo "run_id: $RUN_ID"
echo "prompt: $PROMPT"
echo "monitor: $MONITOR"
echo "monitor_seconds: $MONITOR_SECONDS"

if [ "$DRY_RUN" -eq 1 ]; then
  cat <<EOF

dry-run，不会启动 tmux。
实际执行会通过 AgentRun 运行：
  agentrun session start --project "$PROJECT" --profile "$PROFILE" --run-id "$RUN_ID" --cwd "$ROOT_DIR" --force
  agentrun session send "$RUN_ID" --project "$PROJECT" --text "$PROMPT"
  agentrun session logs "$RUN_ID" --project "$PROJECT" --tail 120

其中 session start 会使用 AgentRun tmux profile 的默认参数，等待 TUI 输出稳定后返回。
EOF
  exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux 不可用，请先安装 tmux。" >&2
  exit 1
fi

echo ""
echo "验证 AgentRun tmux profile..."
validate_json="$(run_agentrun config validate --project "$PROJECT" --profile "$PROFILE")"
if ! printf '%s' "$validate_json" | json_ok; then
  echo "profile 验证失败：" >&2
  printf '%s\n' "$validate_json" >&2
  exit 1
fi

echo "通过 AgentRun 启动后台 tmux session..."
start_json="$(run_agentrun session start --project "$PROJECT" --profile "$PROFILE" --run-id "$RUN_ID" --cwd "$ROOT_DIR" --force)"
tmux_session="$(printf '%s' "$start_json" | json_get session)"
window_name="$(printf '%s' "$start_json" | json_get window_name)"
pane_id="$(printf '%s' "$start_json" | json_get pane_id)"
ready="$(printf '%s' "$start_json" | json_get ready)"
ready_reason="$(printf '%s' "$start_json" | json_get ready_reason)"
validate_tmux_session_name "$tmux_session"

echo "通过 AgentRun 投递 prompt..."
if ! send_json="$(run_agentrun session send "$RUN_ID" --project "$PROJECT" --text "$PROMPT")"; then
  echo "prompt 投递失败：" >&2
  printf '%s\n' "$send_json" >&2
  exit 1
fi

cat <<EOF

已通过 AgentRun 投递到后台 tmux。
tmux_session: $tmux_session
tmux_window: $window_name
pane_id: $pane_id
ready: $ready
ready_reason: $ready_reason

tmux 常用命令：
  tmux ls
  tmux attach -t "$tmux_session"
  tmux list-windows -t "$tmux_session"
  tmux capture-pane -p -t "$pane_id" -S -120
  tmux kill-session -t "$tmux_session"

AgentRun 常用命令：
  PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun --json session status "$RUN_ID" --project "$PROJECT"
  PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun --json session logs "$RUN_ID" --project "$PROJECT" --tail 120
EOF

if [ "$MONITOR" -eq 1 ]; then
  monitor_logs "$RUN_ID" "$pane_id"
fi
