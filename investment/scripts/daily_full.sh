#!/usr/bin/env bash
# 全量日常：投顾 + 量化（不含纸面调仓与 Agent 回归）

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python3 research/daily_run.py --preset full --json
