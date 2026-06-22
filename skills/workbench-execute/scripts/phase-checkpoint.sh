#!/usr/bin/env bash
set -euo pipefail

PHASE="${1:-P0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

echo "# ${PHASE} checkpoint"
echo
echo "## git status --short"
git status --short

echo
echo "## git diff --stat"
git diff --stat || true

echo
echo "## validation hint"
echo "Run: bash scripts/validate.sh --quick"
