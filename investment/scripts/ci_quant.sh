#!/usr/bin/env bash
# 本地量化 CI 镜像（与 GitHub Actions investment-ci 对齐，P22.2 / P40）

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[ci_quant] unit tests…"
python3 -m unittest discover -s tests -v

echo "[ci_quant] frontend JS gate…"
python3 scripts/check_frontend_js.py

echo "[ci_quant] quant import audit…"
bash scripts/check_quant_imports.sh

echo "[ci_quant] repro evals…"
python3 evals/run_repro.py

echo "[ci_quant] golden checklist + presets…"
python3 evals/run_checklist.py --mock --presets

echo "[ci_quant] daily_run eval-mock…"
python3 research/daily_run.py --eval-mock

echo "[ci_quant] OK"
