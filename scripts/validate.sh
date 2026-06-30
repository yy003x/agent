#!/usr/bin/env bash
# 启动自检脚本
# 用法：
#   bash scripts/validate.sh          # quick：结构 / 语法 / 配置 / CLI
#   bash scripts/validate.sh --quick
#   bash scripts/validate.sh --e2e     # quick + 外部依赖 / LanceDB
# 退出码：0=通过，非0=失败

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  export PATH="$ROOT_DIR/.venv/bin:$PATH"
fi

MODE="${1:---quick}"
case "$MODE" in
  --quick|quick) MODE="quick" ;;
  --e2e|e2e) MODE="e2e" ;;
  -h|--help)
    sed -n '1,8p' "$0"
    exit 0
    ;;
  *)
    echo "未知参数：$1"
    echo "用法：bash scripts/validate.sh [--quick|--e2e]"
    exit 2
    ;;
esac

PASS=0
FAIL=0
AGENTRUN_APP_ROOT="$ROOT_DIR/apps/agentrun"
if [ -d "$AGENTRUN_APP_ROOT/src/agentrun" ]; then
  AGENTRUN_PY_ROOT="$AGENTRUN_APP_ROOT/src"
  AGENTRUN_GATEWAY_PATH="apps/agentrun/src/agentrun_workbench"
  AGENTRUN_SCRIPT_PATH="apps/agentrun/bin/build-state.py"
else
  AGENTRUN_PY_ROOT="$AGENTRUN_APP_ROOT"
  AGENTRUN_GATEWAY_PATH="apps/agentrun"
  AGENTRUN_SCRIPT_PATH="apps/agentrun/scripts/*.py"
fi
if [ -d "$ROOT_DIR/apps/api/src/agent_workbench_api" ]; then
  API_PY_ROOT="$ROOT_DIR/apps/api/src"
  API_COMPILE_PATH="apps/api/src"
  API_MAIN_IMPORT="agent_workbench_api.main"
else
  API_PY_ROOT="$ROOT_DIR"
  API_COMPILE_PATH="apps/api"
  API_MAIN_IMPORT="apps.api.main"
fi
if [ -d "$ROOT_DIR/apps/workflows/src/agent_workflows" ]; then
  WORKFLOWS_PY_ROOT="$ROOT_DIR/apps/workflows/src"
  WORKFLOWS_COMPILE_PATH="apps/workflows/src"
else
  WORKFLOWS_PY_ROOT="$ROOT_DIR"
  WORKFLOWS_COMPILE_PATH="apps/workflows"
fi
if [ -d "$ROOT_DIR/apps/content-runtime/src/agent_content_runtime" ]; then
  CONTENT_RUNTIME_PY_ROOT="$ROOT_DIR/apps/content-runtime/src"
  CONTENT_RUNTIME_COMPILE_PATH="apps/content-runtime/src"
  CONTENT_RUNTIME_BIN="apps/content-runtime/bin/content-runtime"
else
  CONTENT_RUNTIME_PY_ROOT="$ROOT_DIR"
  CONTENT_RUNTIME_COMPILE_PATH="skills/content-generate/scripts"
  CONTENT_RUNTIME_BIN="skills/content-generate/scripts/content_runtime.py"
fi
if [ -d "$ROOT_DIR/apps/state-sync/src/agent_state_sync" ]; then
  STATE_SYNC_PY_ROOT="$ROOT_DIR/apps/state-sync/src"
  STATE_SYNC_COMPILE_PATH="apps/state-sync/src"
  STATE_SYNC_BIN="apps/state-sync/bin/state-sync"
else
  STATE_SYNC_PY_ROOT="$ROOT_DIR"
  STATE_SYNC_COMPILE_PATH="scripts/state_sync.py"
  STATE_SYNC_BIN="scripts/state_sync.py"
fi
if [ -d "$ROOT_DIR/apps/agent-memory/src/agent_memory" ]; then
  WORKBENCH_MEMORY_PY_ROOT="$ROOT_DIR/apps/agent-memory/src"
  WORKBENCH_MEMORY_COMPILE_PATH="apps/agent-memory/src"
  FINALIZE_BIN="apps/agent-memory/bin/finalize"
  LEARNING_BIN="apps/agent-memory/bin/agent-learning-review"
else
  WORKBENCH_MEMORY_PY_ROOT="$ROOT_DIR"
  WORKBENCH_MEMORY_COMPILE_PATH="scripts/finalize.py scripts/agent_learning_review.py"
  FINALIZE_BIN="scripts/finalize.py"
  LEARNING_BIN="scripts/agent_learning_review.py"
fi
if [ -d "$ROOT_DIR/apps/scheduler/src/agent_scheduler" ]; then
  SCHEDULER_PY_ROOT="$ROOT_DIR/apps/scheduler/src"
  SCHEDULER_FILE="apps/scheduler/src/agent_scheduler/scheduler.py"
  SCHEDULER_JOBS_FILE="apps/scheduler/conf/jobs.json"
else
  SCHEDULER_PY_ROOT="$ROOT_DIR"
  SCHEDULER_FILE="apps/scheduler/scheduler.py"
  SCHEDULER_JOBS_FILE="apps/scheduler/jobs.json"
fi
APP_PYTHONPATH="$API_PY_ROOT:$WORKFLOWS_PY_ROOT:$CONTENT_RUNTIME_PY_ROOT:$STATE_SYNC_PY_ROOT:$WORKBENCH_MEMORY_PY_ROOT:$AGENTRUN_PY_ROOT:$SCHEDULER_PY_ROOT:$ROOT_DIR"
AGENTRUN_CONF_DIR="$ROOT_DIR/config/agentrun"
AGENTRUN_RUNS_DIR="$ROOT_DIR/runs/agentrun"

run_agentrun() {
  PYTHONPATH="$AGENTRUN_PY_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m agentrun.cli.main --conf-dir "$AGENTRUN_CONF_DIR" --runs-dir "$AGENTRUN_RUNS_DIR" "$@"
}

check() {
  local name="$1"
  local cmd="$2"
  if ( eval "$cmd" ) &>/dev/null; then
    echo "  ✓ $name"
    PASS=$((PASS+1))
  else
    echo "  ✗ $name"
    FAIL=$((FAIL+1))
  fi
}

echo "=== Agent 启动自检（${MODE}）==="
echo ""

echo "[基础环境]"
check "Python 3.11+" "python3 -c 'import sys; assert sys.version_info >= (3,11)'"

echo ""
echo "[项目文件]"
check "AGENTS.md 存在" "test -f AGENTS.md"
check "Makefile 存在" "test -f Makefile"
check "rules/core-routing.md 存在" "test -f rules/core-routing.md"
check "rules/core-safety.md 存在" "test -f rules/core-safety.md"
check "skills/workbench-chat/SKILL.md 存在" "test -f skills/workbench-chat/SKILL.md"
check "skills/knowledge-search/SKILL.md 存在" "test -f skills/knowledge-search/SKILL.md"
check "skills/workbench-research/SKILL.md 存在" "test -f skills/workbench-research/SKILL.md"
check "skills/workbench-design/SKILL.md 存在" "test -f skills/workbench-design/SKILL.md"
check "skills/workbench-execute/SKILL.md 存在" "test -f skills/workbench-execute/SKILL.md"
check "skills/workbench-finalizer/SKILL.md 存在" "test -f skills/workbench-finalizer/SKILL.md"
check "skills/agent-learn/SKILL.md 存在" "test -f skills/agent-learn/SKILL.md"
check "skills/agent-skill-create/SKILL.md 存在" "test -f skills/agent-skill-create/SKILL.md"
check "skills/book-asset/SKILL.md 存在" "test -f skills/book-asset/SKILL.md"
check "skills/knowledge-sync/SKILL.md 存在" "test -f skills/knowledge-sync/SKILL.md"
check "skills/book-profile/SKILL.md 存在" "test -f skills/book-profile/SKILL.md"
check "skills/book-campaign/SKILL.md 存在" "test -f skills/book-campaign/SKILL.md"
check "skills/content-package/SKILL.md 存在" "test -f skills/content-package/SKILL.md"
check "skills/content-compliance-review/SKILL.md 存在" "test -f skills/content-compliance-review/SKILL.md"
check "skills/book-media/SKILL.md 存在" "test -f skills/book-media/SKILL.md"
check "skills/workbench-session-ops/SKILL.md 存在" "test -f skills/workbench-session-ops/SKILL.md"
check "skills/content-generate/SKILL.md 存在" "test -f skills/content-generate/SKILL.md"
check "apps/agent-memory/bin/finalize 存在" "test -f apps/agent-memory/bin/finalize"
check "apps/agent-memory/bin/agent-learning-review 存在" "test -f apps/agent-memory/bin/agent-learning-review"
check "apps/state-sync/bin/state-sync 存在" "test -f apps/state-sync/bin/state-sync"
check "scripts/workbench_service.py 存在" "test -f scripts/workbench_service.py"
check "scripts/model_backend_smoke.py 存在" "test -f scripts/model_backend_smoke.py"
check "scripts/workbench_smoke.py 存在" "test -f scripts/workbench_smoke.py"
check "scripts/runtime_smoke.py 存在" "test -f scripts/runtime_smoke.py"
check "apps/*/app.json 存在" "if find apps -mindepth 2 -maxdepth 2 -name app.json -print -quit | grep -q .; then test -f apps/agentrun/app.json && test -f apps/api/app.json && test -f apps/content-runtime/app.json && test -f apps/scheduler/app.json && test -f apps/state-sync/app.json && test -f apps/web/app.json && test -f apps/agent-memory/app.json && test -f apps/workflows/app.json; else true; fi"
check "scheduler jobs.json 存在" "test -f \"$SCHEDULER_JOBS_FILE\""
check "无旧版本/历史包袱残留" "command -v rg && ! rg -n 'apps/workbench|apps/agent(/|$)|legacy-tmux|replace-legacy-tmux|/api/runtime/tmux|legacy|兼容|旧版|旧实现|旧启动|旧命令|历史|旧|迁移|P6|migration|fallback|allowed_providers' Makefile scripts apps skills design rules requirements.txt AGENTS.md memory --glob '!scripts/validate.sh' --glob '!apps/web/package-lock.json' --glob '!apps/web/node_modules/**' --glob '!apps/web/dist/**'"

echo ""
echo "[脚本语法]"
check "agent-memory app 语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m compileall -q $WORKBENCH_MEMORY_COMPILE_PATH && python3 -m py_compile \"$FINALIZE_BIN\" \"$LEARNING_BIN\""
check "agent-memory wrappers 语法正常" "python3 -m py_compile scripts/finalize.py scripts/agent_learning_review.py"
check "state-sync app 语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m compileall -q $STATE_SYNC_COMPILE_PATH && python3 -m py_compile \"$STATE_SYNC_BIN\""
check "state-sync wrapper 语法正常" "python3 -m py_compile scripts/state_sync.py"
check "workbench_service.py 语法正常" "python3 -m py_compile scripts/workbench_service.py"
check "model_backend_smoke.py 语法正常" "python3 -m py_compile scripts/model_backend_smoke.py"
check "workbench_smoke.py 语法正常" "python3 -m py_compile scripts/workbench_smoke.py"
check "runtime_smoke.py 语法正常" "python3 -m py_compile scripts/runtime_smoke.py"
check "content-runtime app 语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m compileall -q \"$CONTENT_RUNTIME_COMPILE_PATH\" && python3 -m py_compile \"$CONTENT_RUNTIME_BIN\""
check "content-runtime skill wrapper 语法正常" "python3 -m py_compile skills/content-generate/scripts/content_runtime.py"
check "agent skill 脚手架语法正常" "python3 -m py_compile skills/agent-skill-create/scripts/scaffold_skill.py"
check "scheduler.py 语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m py_compile \"$SCHEDULER_FILE\""
check "workflow 编排层语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m compileall -q \"$WORKFLOWS_COMPILE_PATH\""
check "agentrun gateway 包语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m compileall -q \"$AGENTRUN_GATEWAY_PATH\""
check "agentrun 附带脚本语法正常" "python3 -m py_compile $AGENTRUN_SCRIPT_PATH"
check "agentrun 本地包语法正常" "PYTHONPATH=\"$AGENTRUN_PY_ROOT\" python3 -m compileall -q \"$AGENTRUN_PY_ROOT/agentrun\" apps/agentrun/tests"
check "FastAPI 工作台语法正常" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -m compileall -q \"$API_COMPILE_PATH\""
check "Web 工作台配置存在" "test -f apps/web/package.json && test -f apps/web/src/App.tsx"
if [ -d apps/web/node_modules ]; then
  check "Web 工作台 typecheck" "cd apps/web && npm run typecheck"
  check "Web 工作台 build" "cd apps/web && npm run build"
else
  check "Web 依赖未安装时跳过构建" "test -f apps/web/package.json"
fi

echo ""
echo "[配置格式]"
check "apps/*/app.json 可解析" "python3 -c 'import json, pathlib; [json.load(open(p)) for p in pathlib.Path(\"apps\").glob(\"*/app.json\")]'"
check "scheduler jobs.json 可解析" "python3 -c 'import json; json.load(open(\"$SCHEDULER_JOBS_FILE\"))'"
check "config/state-sync.example.json 可解析" "python3 -c 'import json; json.load(open(\"config/state-sync.example.json\"))'"
check "config/model_tests.example.json 可解析" "python3 -c 'import json; json.load(open(\"config/model_tests.example.json\"))'"
check ".claude/settings.json 可解析" "python3 -c 'import json; json.load(open(\".claude/settings.json\"))'"
check "claude settings example 可解析" "python3 -c 'import json; json.load(open(\"config/claude-settings.example.json\"))'"
check "agentrun runtime.yaml 存在" "test -f config/agentrun/runtime.yaml"
check "agentrun provider 配置存在" "test -f config/agentrun/providers/api.yaml && test -f config/agentrun/providers/cli.yaml && test -f config/agentrun/providers/tmux.yaml"

echo ""
echo "[工作目录]"
check "workspace/kb/ 存在" "test -d workspace/kb"
check "workspace/daily/ 存在" "test -d workspace/daily"
check "workspace/agent-learning/ 存在" "test -d workspace/agent-learning"
check "outputs/ 存在" "test -d outputs"

echo ""
echo "[content-runtime CLI]"
check "content-runtime --help 可执行" "python3 \"$CONTENT_RUNTIME_BIN\" --help"
check "content-runtime kb search --help 可执行" "python3 \"$CONTENT_RUNTIME_BIN\" kb search --help"
check "content-runtime text draft --help 可执行" "python3 \"$CONTENT_RUNTIME_BIN\" text draft --help"
check "content-runtime plan build --help 可执行" "python3 \"$CONTENT_RUNTIME_BIN\" plan build --help"
check "content-runtime module --help 可执行" "PYTHONPATH=\"$CONTENT_RUNTIME_PY_ROOT\" python3 -m agent_content_runtime --help"
check "agent-memory finalize --help 可执行" "python3 \"$FINALIZE_BIN\" --help"
check "agent-memory learning --dry-run 可执行" "python3 \"$LEARNING_BIN\" --dry-run"
check "agent-memory learning promote --help 可执行" "python3 \"$LEARNING_BIN\" promote --help"
check "state-sync --help 可执行" "python3 \"$STATE_SYNC_BIN\" --help"
check "state-sync plan 可执行" "python3 \"$STATE_SYNC_BIN\" plan --limit 3"
check "workbench_service.py --help 可执行" "python3 scripts/workbench_service.py --help"
check "workbench_service.py list 可执行" "python3 scripts/workbench_service.py list"
check "make help 可执行" "make help"
check "make status 可执行" "make status"
check "FastAPI app 可导入" "PYTHONPATH=\"$APP_PYTHONPATH\" python3 -c 'mod=__import__(\"$API_MAIN_IMPORT\", fromlist=[\"app\"]); assert mod.app.title'"
check "workbench_smoke.py --help 可执行" "python3 scripts/workbench_smoke.py --help"
check "model_backend_smoke.py --help 可执行" "python3 scripts/model_backend_smoke.py --help"
check "model_backend_smoke.py --list 可执行" "python3 scripts/model_backend_smoke.py --list"
check "runtime_smoke.py --help 可执行" "python3 scripts/runtime_smoke.py --help"

echo ""
echo "[agentrun]"
check "agentrun 源码存在" "test -d \"$AGENTRUN_PY_ROOT/agentrun\""
check "agentrun 单测通过" "PYTHONPATH=\"$AGENTRUN_PY_ROOT\" python3 -m unittest discover -s apps/agentrun/tests -v"
check "agentrun doctor 可执行" "run_agentrun --json doctor"
check "agentrun profiles 可读取" "run_agentrun --json profiles"
check "agentrun cli 配置可验证" "run_agentrun config validate --project agent --provider cli --profile codex-cli --json"
check "agentrun 已验证 choices 可读取" "run_agentrun config choices --project agent --json"

if [ "$MODE" = "e2e" ]; then
  echo ""
  echo "[外部依赖]"
  check "ffmpeg 可用" "ffmpeg -version"
  check "codex CLI 可执行" "command -v codex"
  check "lancedb 已安装" "python3 -c 'import lancedb'"
  check "sentence-transformers 已安装" "python3 -c 'import sentence_transformers'"
  check "jieba 已安装" "python3 -c 'import jieba'"
  check "apscheduler 已安装" "python3 -c 'import apscheduler'"
  check "pillow 已安装" "python3 -c 'import PIL'"

  echo ""
  echo "[KB 层]"
  check "LanceDB lance/ 可访问" "python3 -c 'import lancedb; lancedb.connect(\"workspace/kb/lance\")'"

  echo ""
  echo "[运营闭环 smoke]"
  check "最小文本运营闭环通过" "python3 scripts/workbench_smoke.py --quiet"
fi

echo ""
echo "=== 结果：${PASS} 通过 / ${FAIL} 失败 ==="
if [ "$FAIL" -gt 0 ]; then
  echo "请修复上述失败项后重新运行"
  exit 1
else
  echo "全部通过，Agent 可以启动"
  exit 0
fi
