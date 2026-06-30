#!/usr/bin/env bash
# 通过 AgentRun tmux session 投递一句话到真实 CLI runtime。

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENTRUN_APP="$ROOT_DIR/apps/agentrun"
CONF_DIR="$ROOT_DIR/config/agentrun"
RUNS_DIR="$ROOT_DIR/runs/agentrun"
PROJECT="${AGENTRUN_TMUX_PROJECT:-agent}"
PROFILE="${AGENTRUN_TMUX_PROFILE:-tmux-codex}"
RUN_ID="${AGENTRUN_TMUX_RUN_ID:-session-project-summary-$(date +%Y%m%d-%H%M%S)-$$}"
PROMPT="看下当前项目实现了什么"
DRY_RUN=0

usage() {
  cat <<'EOF'
用法：
  scripts/tmux_project_summary.sh
  scripts/tmux_project_summary.sh "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --profile tmux-claude "看下当前项目实现了什么"
  scripts/tmux_project_summary.sh --dry-run

环境变量：
  AGENTRUN_TMUX_PROJECT   默认 agent
  AGENTRUN_TMUX_PROFILE   默认 tmux-codex
  AGENTRUN_TMUX_RUN_ID    默认自动生成
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
    --profile)
      PROFILE="${2:?--profile 需要 profile id，例如 tmux-codex}"
      shift 2
      ;;
    --project)
      PROJECT="${2:?--project 需要 project id}"
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

json_value() {
  local json_input
  json_input="$(cat)"
  JSON_INPUT="$json_input" "$PYTHON_BIN" - "$1" <<'PY'
import json
import os
import sys

key = sys.argv[1]
data = json.loads(os.environ.get("JSON_INPUT") or "{}")
value = data.get(key, "")
print("" if value is None else value)
PY
}

json_ok() {
  local json_input
  json_input="$(cat)"
  JSON_INPUT="$json_input" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys

data = json.loads(os.environ.get("JSON_INPUT") or "{}")
raise SystemExit(0 if data.get("ok") else 1)
PY
}

echo "runtime: tmux"
echo "profile: $PROFILE"
echo "project: $PROJECT"
echo "run_id: $RUN_ID"
echo "prompt: $PROMPT"

if [ "$DRY_RUN" -eq 1 ]; then
  cat <<EOF

dry-run，不会启动 tmux。
实际执行会先验证 profile，然后运行：
  agentrun session start --project "$PROJECT" --profile "$PROFILE" --run-id "$RUN_ID" --cwd "$ROOT_DIR"
  agentrun session send "$RUN_ID" --project "$PROJECT" --text "$PROMPT"
EOF
  exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux 不可用，请先安装 tmux。" >&2
  exit 1
fi

echo ""
echo "验证 tmux profile..."
validate_json="$(run_agentrun config validate --project "$PROJECT" --profile "$PROFILE")"
if ! printf '%s' "$validate_json" | json_ok; then
  echo "profile 验证失败：" >&2
  printf '%s\n' "$validate_json" >&2
  exit 1
fi

echo "启动 tmux session..."
start_json="$(run_agentrun session start --project "$PROJECT" --profile "$PROFILE" --run-id "$RUN_ID" --cwd "$ROOT_DIR" --force)"
tmux_session="$(printf '%s' "$start_json" | json_value session)"
pane_id="$(printf '%s' "$start_json" | json_value pane_id)"

echo "投递 prompt..."
run_agentrun session send "$RUN_ID" --project "$PROJECT" --text "$PROMPT" >/dev/null

cat <<EOF

已投递到 tmux runtime。
tmux_session: ${tmux_session:-agentrun}
pane_id: ${pane_id:-unknown}

查看窗口：
  tmux attach -t ${tmux_session:-agentrun}

查看状态：
  PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun --json session status "$RUN_ID" --project "$PROJECT"

查看日志：
  PYTHONPATH=apps/agentrun python3 -m agentrun.cli.main --conf-dir config/agentrun --runs-dir runs/agentrun --json session logs "$RUN_ID" --project "$PROJECT" --tail 120
EOF
