#!/usr/bin/env python3
"""short 参数扫描 CLI（min_score × horizon_days）。"""


import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.backtest.engine import scan_signal_parameters, scan_signal_parameters_oos  # noqa: E402
from core.data.facade import bars_and_source as fetch_daily_bars  # noqa: E402
from core.data.facade import get_quote  # noqa: E402


def _parse_float_list(text: str) -> list:
    out = []
    for part in (text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    return out


def _parse_int_list(text: str) -> list:
    out = []
    for part in (text or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def run_scan(
    code: str,
    *,
    lookback: int = 120,
    min_scores: list,
    horizons: list,
    top: int = 10,
    oos: bool = False,
    apply_costs: bool = False,
    data_mode: str = "full",
) -> dict:
    quote = get_quote(code)
    name = quote.get("stock_name") if quote.get("success") else code
    sym = quote.get("stock_code") if quote.get("success") else code
    bars, src = fetch_daily_bars(code, limit=lookback + 35)
    if not bars and quote.get("success"):
        bars, src = fetch_daily_bars(sym, limit=lookback + 35)
    if not bars:
        return {
            "success": False,
            "error": f"无法获取 {code} 日线",
            "stock_code": sym,
            "stock_name": name,
        }

    if oos:
        oos_result = scan_signal_parameters_oos(
            bars,
            min_scores=min_scores,
            horizon_days_list=horizons,
            apply_costs=apply_costs,
            data_mode=data_mode,
        )
        return {
            "success": bool(oos_result.get("success")),
            "stock_code": sym,
            "stock_name": name,
            "data_source": src,
            "lookback_bars": len(bars),
            "oos": oos_result,
        }

    rows = scan_signal_parameters(
        bars,
        min_scores=min_scores,
        horizon_days_list=horizons,
        apply_costs=apply_costs,
        data_mode=data_mode,
    )
    return {
        "success": True,
        "stock_code": sym,
        "stock_name": name,
        "data_source": src,
        "lookback_bars": len(bars),
        "scan_count": len(rows),
        "top": rows[:top],
        "all": rows,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Investment short 参数扫描")
    parser.add_argument("--code", default="茅台", help="标的代码或名称")
    parser.add_argument("--lookback", type=int, default=120, help="日线长度")
    parser.add_argument(
        "--min-scores",
        default="50,55,60,65,70",
        help="逗号分隔的 min_score 列表",
    )
    parser.add_argument(
        "--horizons",
        default="2,3,5",
        help="逗号分隔的 horizon_days 列表",
    )
    parser.add_argument("--top", type=int, default=10, help="输出前 N 组参数")
    parser.add_argument("--oos", action="store_true", help="样本外切分搜参（P7.5）")
    parser.add_argument("--apply-costs", action="store_true", help="扣除简化交易成本")
    parser.add_argument(
        "--data-mode",
        default="full",
        choices=["full", "quote_fallback"],
        help="回测评分数据模式",
    )
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    args = parser.parse_args(argv)

    result = run_scan(
        args.code,
        lookback=args.lookback,
        min_scores=_parse_float_list(args.min_scores),
        horizons=_parse_int_list(args.horizons),
        top=args.top,
        oos=bool(args.oos),
        apply_costs=bool(args.apply_costs),
        data_mode=args.data_mode,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") else 1

    if not result.get("success"):
        print(result.get("error") or "scan failed")
        return 1

    print(
        f"{result['stock_name']}({result['stock_code']}) "
        f"bars={result['lookback_bars']} src={result['data_source']}"
    )
    print(f"{'horizon':>8} {'min_score':>10} {'trades':>7} {'win%':>7} "
          f"{'avg%':>8} {'total%':>9} {'sharpe':>8}")
    for row in result.get("top") or []:
        print(
            f"{row.get('horizon_days'):>8} "
            f"{row.get('min_score'):>10.0f} "
            f"{row.get('trade_count') or 0:>7} "
            f"{row.get('win_rate_pct') or '-':>7} "
            f"{row.get('avg_return_pct') or '-':>8} "
            f"{row.get('total_return_pct') or '-':>9} "
            f"{row.get('sharpe_approx') or '-':>8}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
