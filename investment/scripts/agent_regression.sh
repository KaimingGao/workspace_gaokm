#!/usr/bin/env bash
# 可选 Agent 周末回归（P24.3，需 DASHSCOPE_API_KEY，不进 PR CI）

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "[agent_regression] mock skills + Agent (20 cases)…"
python3 evals/run_agent_check.py "$@"

echo "[agent_regression] OK"
