#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

echo "## git status --short"
git status --short

echo
echo "## git diff --stat"
git diff --stat || true
