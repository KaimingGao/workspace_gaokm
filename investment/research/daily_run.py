#!/usr/bin/env python3
"""每日自动化：纸面观察池 + 可选 golden checklist（供 cron / launchd 调用）。"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.env import load_env_file  # noqa: E402
from core.paths import ROOT_DIR  # noqa: E402
from quant.ops.daily_presets import DAILY_PRESETS  # noqa: E402
from services.daily_service import DailyRunService  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment 每日任务")
    parser.add_argument(
        "--preset",
        choices=sorted(DAILY_PRESETS.keys()),
        help="任务组合：advisor | quant | full",
    )
    parser.add_argument("--paper-run", action="store_true", help="跑纸面观察池并记快照")
    parser.add_argument("--paper-buy", action="store_true", help="纸面 run + 模拟买入")
    parser.add_argument("--eval-mock", action="store_true", help="离线 golden checklist（mock）")
    parser.add_argument("--eval-agent", action="store_true", help="全量 Agent 回归（需 LLM）")
    parser.add_argument("--quant-report", action="store_true", help="量化 IC + TopK 摘要报告")
    parser.add_argument("--watching-refresh", action="store_true", help="刷新 watching watchlist")
    parser.add_argument("--cross-section", action="store_true", help="横截面排序 Top N")
    parser.add_argument("--sync-paper-watchlist", action="store_true", help="兼容空步骤（观察/仓位已分离）")
    parser.add_argument("--paper-rebalance", action="store_true", help="横截面 TopK 纸面调仓")
    parser.add_argument(
        "--export-quant-report",
        action="store_true",
        help="quant 报告额外导出 Markdown/HTML 到 data/reports/",
    )
    parser.add_argument(
        "--portfolio-neutral-compare",
        action="store_true",
        help="量化日报嵌入中性化 vs 绝对分对照摘要",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    load_env_file(os.path.join(ROOT_DIR, ".env"))

    flag_names = [
        "paper_run",
        "paper_buy",
        "eval_mock",
        "eval_agent",
        "quant_report",
        "watching_refresh",
        "cross_section",
        "sync_paper_watchlist",
        "paper_rebalance",
        "export_quant_report",
        "portfolio_neutral_compare",
    ]
    explicit = {name: bool(getattr(args, name)) for name in flag_names}
    if not args.preset and not any(explicit.values()):
        parser.error("请指定 --preset advisor|quant|full，或至少一项 --paper-run / --eval-mock 等")

    overrides = {k: v for k, v in explicit.items() if v}
    out = DailyRunService().run(preset=args.preset, **overrides)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        if out.get("preset"):
            print(f"preset: {out['preset']}")
        for step in out.get("steps") or []:
            flag = "OK" if step.get("ok") else "FAIL"
            print(f"[{step['name']}] {flag}")
            if step.get("error"):
                print(f"  {step['error']}")
            export = step.get("export") or {}
            for fmt, path in (export.get("paths") or {}).items():
                print(f"  export {fmt}: {path}")

    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
