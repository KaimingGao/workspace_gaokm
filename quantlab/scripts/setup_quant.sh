#!/usr/bin/env bash
# 量化研究台一键初始化：watching + 纸面账户（P21.4）
# 用法：cd quantlab && bash scripts/setup_quant.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[setup_quant] 初始化 watching…"
if [[ -f data/watching.json ]]; then
  echo "  skip: data/watching.json 已存在"
else
  python3 research/watching_run.py --init
fi

echo "[setup_quant] 刷新 watchlist…"
python3 research/watching_run.py --refresh --json || true

echo "[setup_quant] 初始化纸面账户…"
if [[ -f data/paper.json ]]; then
  echo "  skip: data/paper.json 已存在"
else
  python3 research/paper_run.py --init
fi

echo "[setup_quant] 完成。建议下一步："
echo "  bash scripts/daily_quant.sh"
echo "  bash scripts/daily_quant_paper.sh   # 含量化+纸面调仓"
echo "  python3 run_web.py                # Web 量化面板"
