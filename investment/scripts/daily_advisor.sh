#!/usr/bin/env bash
# 投顾日常：纸面观察池 + 离线 golden checklist
# 用法：crontab 示例见 docs/quant-ops.md

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python3 research/daily_run.py --preset advisor --json
