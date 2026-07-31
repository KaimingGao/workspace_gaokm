#!/usr/bin/env bash
# N5 / P2：纸面日更（run_paper_daily）— 写 DecisionRecord + 五问 + 衰减告警；不代客下单

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# 可选环境变量：PAPER_DAILY_SIMULATE_BUY=1 · PAPER_DAILY_STRATEGY=short

exec python3 - <<'PY'
from core.schedule_jobs import run_paper_daily
import json, os, sys

simulate = os.environ.get("PAPER_DAILY_SIMULATE_BUY", "0").strip() in ("1", "true", "True", "yes")
strategy = (os.environ.get("PAPER_DAILY_STRATEGY") or "short").strip() or "short"
out = run_paper_daily(simulate_buy=simulate, strategy=strategy)
print(json.dumps({
    "ok": out.get("ok"),
    "kind": out.get("kind"),
    "strategy_id": out.get("strategy_id"),
    "cost_model": out.get("cost_model"),
    "buys_blocked": out.get("buys_blocked"),
    "monitor_alerts": out.get("monitor_alerts"),
    "risk_blocks": out.get("risk_blocks"),
    "data_quality": out.get("data_quality"),
    "error": out.get("error"),
    "path": out.get("path"),
    "note": out.get("note"),
}, ensure_ascii=False, indent=2))
sys.exit(0 if out.get("ok") else 1)
PY
