#!/usr/bin/env bash
# 量化研究日常：watching + 横截面 + 量化日报 + Markdown/HTML 导出

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python3 research/daily_run.py --preset quant --json
