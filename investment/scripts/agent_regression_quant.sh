#!/usr/bin/env bash
# 量化专项 Agent 周末回归（P37/P55，9 个 quant_* case，需 DASHSCOPE_API_KEY，不进 PR CI）

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "[agent_regression_quant] mock skills + Agent (10 quant_* cases)…"
python3 evals/run_agent_check.py --quant-only --presets "$@"

echo "[agent_regression_quant] OK"
