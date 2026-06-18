#!/usr/bin/env bash
# 启动自检脚本
# 用法：bash scripts/validate.sh
# 退出码：0=通过，非0=失败

set -e

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

echo "=== Agent 启动自检 ==="
echo ""

echo "[环境依赖]"
check "Python 3.11+" "python3 -c 'import sys; assert sys.version_info >= (3,11)'"
check "ffmpeg 可用" "ffmpeg -version"
check "ANTHROPIC_API_KEY 已设置" "test -n \"\$ANTHROPIC_API_KEY\""
check "chromadb 已安装" "python3 -c 'import chromadb'"
check "apscheduler 已安装" "python3 -c 'import apscheduler'"
check "pillow 已安装" "python3 -c 'import PIL'"

echo ""
echo "[项目文件]"
check "AGENTS.md 存在" "test -f AGENTS.md"
check "rules/core-routing.md 存在" "test -f rules/core-routing.md"
check "rules/core-safety.md 存在" "test -f rules/core-safety.md"
check "skills/content-generate/SKILL.md 存在" "test -f skills/content-generate/SKILL.md"
check "scripts/finalize.py 存在" "test -f scripts/finalize.py"
check "scripts/agent_learning_review.py 存在" "test -f scripts/agent_learning_review.py"
check "apps/scheduler/jobs.json 存在" "test -f apps/scheduler/jobs.json"

echo ""
echo "[脚本语法]"
check "finalize.py 语法正常" "python3 -m py_compile scripts/finalize.py"
check "agent_learning_review.py 语法正常" "python3 -m py_compile scripts/agent_learning_review.py"
check "content_runtime.py 语法正常" "python3 -m py_compile skills/content-generate/scripts/content_runtime.py"
check "scheduler.py 语法正常" "python3 -m py_compile apps/scheduler/scheduler.py"

echo ""
echo "[工作目录]"
check "workspace/kb/ 存在" "test -d workspace/kb"
check "workspace/daily/ 存在" "test -d workspace/daily"
check "workspace/agent-learning/ 存在" "test -d workspace/agent-learning"
check "outputs/ 存在" "test -d outputs"

echo ""
echo "[KB 层]"
check "catalog.db 可读" "python3 -c 'import sqlite3; sqlite3.connect(\"workspace/kb/catalog.db\").execute(\"SELECT 1\")'"
check "ChromaDB vector/ 可访问" "python3 -c 'import chromadb; chromadb.PersistentClient(path=\"workspace/kb/vector\")'"

echo ""
echo "[content-runtime CLI]"
check "content_runtime.py --help 可执行" "python3 skills/content-generate/scripts/content_runtime.py --help"

echo ""
echo "=== 结果：${PASS} 通过 / ${FAIL} 失败 ==="
if [ "$FAIL" -gt 0 ]; then
  echo "请修复上述失败项后重新运行"
  exit 1
else
  echo "全部通过，Agent 可以启动"
  exit 0
fi
