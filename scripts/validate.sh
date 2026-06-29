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
SHARED_RUNTIME_ROOT="${AGENT_SHARED_RUNTIME_ROOT:-$(cd "$ROOT_DIR/.." && pwd)/runtime}"
SHARED_RUNTIME_RUNS_DIR="${AGENT_SHARED_RUNTIME_RUNS_DIR:-$ROOT_DIR/runs/agentrun}"

run_agentrun() {
  PYTHONPATH="$SHARED_RUNTIME_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m agentrun.cli.main --runs-dir "$SHARED_RUNTIME_RUNS_DIR" "$@"
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
check "scripts/finalize.py 存在" "test -f scripts/finalize.py"
check "scripts/agent_learning_review.py 存在" "test -f scripts/agent_learning_review.py"
check "scripts/state_sync.py 存在" "test -f scripts/state_sync.py"
check "scripts/workbench_service.py 存在" "test -f scripts/workbench_service.py"
check "scripts/model_backend_smoke.py 存在" "test -f scripts/model_backend_smoke.py"
check "scripts/workbench_smoke.py 存在" "test -f scripts/workbench_smoke.py"
check "apps/scheduler/jobs.json 存在" "test -f apps/scheduler/jobs.json"
check "无旧版本/历史包袱残留" "command -v rg && ! rg -n 'apps/workbench|apps/agent|legacy-tmux|replace-legacy-tmux|/api/runtime/tmux|legacy|兼容|旧版|旧实现|旧启动|旧命令|历史|旧|迁移|P6|migration|fallback|allowed_providers' Makefile scripts apps skills design rules requirements.txt AGENTS.md memory --glob '!scripts/validate.sh' --glob '!apps/web/package-lock.json' --glob '!apps/web/node_modules/**' --glob '!apps/web/dist/**'"

echo ""
echo "[脚本语法]"
check "finalize.py 语法正常" "python3 -m py_compile scripts/finalize.py"
check "agent_learning_review.py 语法正常" "python3 -m py_compile scripts/agent_learning_review.py"
check "state_sync.py 语法正常" "python3 -m py_compile scripts/state_sync.py"
check "workbench_service.py 语法正常" "python3 -m py_compile scripts/workbench_service.py"
check "model_backend_smoke.py 语法正常" "python3 -m py_compile scripts/model_backend_smoke.py"
check "workbench_smoke.py 语法正常" "python3 -m py_compile scripts/workbench_smoke.py"
check "content_runtime.py 语法正常" "python3 -m py_compile skills/content-generate/scripts/content_runtime.py"
check "agent skill 脚手架语法正常" "python3 -m py_compile skills/agent-skill-create/scripts/scaffold_skill.py"
check "scheduler.py 语法正常" "python3 -m py_compile apps/scheduler/scheduler.py"
check "workflow 编排层语法正常" "python3 -m py_compile apps/workflows/*.py"
check "runtime gateway 包语法正常" "python3 -m py_compile apps/runtime/*.py"
check "shared runtime 包语法正常" "cd \"$SHARED_RUNTIME_ROOT\" && PYTHONPATH=src python3 -m compileall -q src tests"
check "FastAPI 工作台语法正常" "python3 -m py_compile apps/api/*.py apps/api/services/*.py"
check "Web 工作台配置存在" "test -f apps/web/package.json && test -f apps/web/src/App.tsx"
if [ -d apps/web/node_modules ]; then
  check "Web 工作台 typecheck" "cd apps/web && npm run typecheck"
  check "Web 工作台 build" "cd apps/web && npm run build"
else
  check "Web 依赖未安装时跳过构建" "test -f apps/web/package.json"
fi

echo ""
echo "[配置格式]"
check "apps/scheduler/jobs.json 可解析" "python3 -c 'import json; json.load(open(\"apps/scheduler/jobs.json\"))'"
check "config/state-sync.example.json 可解析" "python3 -c 'import json; json.load(open(\"config/state-sync.example.json\"))'"
check "model_tests.example.json 可解析" "python3 -c 'import json; json.load(open(\"model_tests.example.json\"))'"
check ".claude/settings.json 可解析" "python3 -c 'import json; json.load(open(\".claude/settings.json\"))'"
check "claude settings example 可解析" "python3 -c 'import json; json.load(open(\"config/claude-settings.example.json\"))'"

echo ""
echo "[工作目录]"
check "workspace/kb/ 存在" "test -d workspace/kb"
check "workspace/daily/ 存在" "test -d workspace/daily"
check "workspace/agent-learning/ 存在" "test -d workspace/agent-learning"
check "outputs/ 存在" "test -d outputs"

echo ""
echo "[content-runtime CLI]"
check "content_runtime.py --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py --help"
check "content_runtime.py kb search --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py kb search --help"
check "content_runtime.py text draft --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py text draft --help"
check "content_runtime.py plan build --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py plan build --help"
check "finalize.py --help 可执行" "python3 scripts/finalize.py --help"
check "agent_learning_review.py --dry-run 可执行" "python3 scripts/agent_learning_review.py --dry-run"
check "agent_learning_review.py promote --help 可执行" "python3 scripts/agent_learning_review.py promote --help"
check "state_sync.py --help 可执行" "python3 scripts/state_sync.py --help"
check "state_sync.py plan 可执行" "python3 scripts/state_sync.py plan --limit 3"
check "workbench_service.py --help 可执行" "python3 scripts/workbench_service.py --help"
check "workbench_service.py list 可执行" "python3 scripts/workbench_service.py list"
check "make help 可执行" "make help"
check "make status 可执行" "make status"
check "FastAPI app 可导入" "python3 -c 'from apps.api.main import app; assert app.title'"
check "workbench_smoke.py --help 可执行" "python3 scripts/workbench_smoke.py --help"
check "model_backend_smoke.py --help 可执行" "python3 scripts/model_backend_smoke.py --help"
check "model_backend_smoke.py --list 可执行" "python3 scripts/model_backend_smoke.py --list"

echo ""
echo "[shared-runtime]"
check "agentrun 源码存在" "test -d \"$SHARED_RUNTIME_ROOT/src/agentrun\""
check "agentrun 单测通过" "cd \"$SHARED_RUNTIME_ROOT\" && PYTHONPATH=src python3 -m unittest discover -s tests -v"
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
