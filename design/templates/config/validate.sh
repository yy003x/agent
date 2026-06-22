#!/usr/bin/env bash
# 启动自检脚本
# 用法：
#   bash scripts/validate.sh          # quick：结构 / 语法 / 配置 / CLI
#   bash scripts/validate.sh --quick
#   bash scripts/validate.sh --e2e     # quick + 外部依赖 / LanceDB
# 退出码：0=通过，非0=失败

set -e

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

check() {
  local name="$1"
  local cmd="$2"
  if eval "$cmd" &>/dev/null; then
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
check "apps/scheduler/jobs.json 存在" "test -f apps/scheduler/jobs.json"

echo ""
echo "[脚本语法]"
check "finalize.py 语法正常" "python3 -m py_compile scripts/finalize.py"
check "agent_learning_review.py 语法正常" "python3 -m py_compile scripts/agent_learning_review.py"
check "content_runtime.py 语法正常" "python3 -m py_compile skills/content-generate/scripts/content_runtime.py"
check "agent skill 脚手架语法正常" "python3 -m py_compile skills/agent-skill-create/scripts/scaffold_skill.py"
check "scheduler.py 语法正常" "python3 -m py_compile apps/scheduler/scheduler.py"
check "orchestrator.py 语法正常" "python3 -m py_compile apps/agent/orchestrator.py"
check "brain.py 语法正常" "python3 -m py_compile apps/agent/brain.py"
check "workbench 语法正常" "python3 -m py_compile apps/workbench/server.py apps/workbench/health.py apps/workbench/file_browser.py apps/workbench/runtime/*.py"

echo ""
echo "[配置格式]"
check "apps/scheduler/jobs.json 可解析" "python3 -c 'import json; json.load(open(\"apps/scheduler/jobs.json\"))'"
check ".claude/settings.json 可解析" "python3 -c 'import json; json.load(open(\".claude/settings.json\"))'"
check "template settings 可解析" "python3 -c 'import json; json.load(open(\"design/templates/config/settings-claude-code.json\"))'"

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
check "content_runtime.py kb legacy --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py kb legacy --help"
check "content_runtime.py text draft --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py text draft --help"
check "content_runtime.py plan build --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py plan build --help"
check "finalize.py --help 可执行" "python3 scripts/finalize.py --help"
check "agent_learning_review.py --dry-run 可执行" "python3 scripts/agent_learning_review.py --dry-run"
check "agent_learning_review.py promote --help 可执行" "python3 scripts/agent_learning_review.py promote --help"

if [ "$MODE" = "e2e" ]; then
  echo ""
  echo "[外部依赖]"
  check "ffmpeg 可用" "ffmpeg -version"
  check "codex 或 claude CLI 可执行" "command -v codex >/dev/null 2>&1 || command -v claude >/dev/null 2>&1"
  check "lancedb 已安装" "python3 -c 'import lancedb'"
  check "sentence-transformers 已安装" "python3 -c 'import sentence_transformers'"
  check "jieba 已安装" "python3 -c 'import jieba'"
  check "apscheduler 已安装" "python3 -c 'import apscheduler'"
  check "pillow 已安装" "python3 -c 'import PIL'"

  echo ""
  echo "[KB 层]"
  check "LanceDB lance/ 可访问" "python3 -c 'import lancedb; lancedb.connect(\"workspace/kb/lance\")'"
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
