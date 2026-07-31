#!/usr/bin/env bash
# 量化研究 + 纸面横截面调仓（显式 opt-in，非实盘）

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python3 research/daily_run.py --preset quant_paper --json
