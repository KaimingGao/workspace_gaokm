#!/usr/bin/env bash
# 读取 daily_last_run.json，失败时 exit 1（配合 cron MAILTO）
# 用法：30 17 * * 1-5 cd /path/to/quantlab && bash scripts/daily_quant.sh && bash scripts/daily_check.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

exec python3 research/daily_check.py "$@"
