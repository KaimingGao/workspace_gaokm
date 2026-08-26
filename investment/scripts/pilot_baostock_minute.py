#!/usr/bin/env python3
"""试点：BaoStock 5m 深度 vs 东财（默认 3 只）。

用法（在 investment 目录）：
  python3 scripts/pilot_baostock_minute.py
  python3 scripts/pilot_baostock_minute.py 600519 000001 600000
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _span_days(bars) -> int:
    from skills.common.minute_history import group_minute_bars_by_date

    return len(group_minute_bars_by_date(bars or []) or {})


def main() -> int:
    parser = argparse.ArgumentParser(description="BaoStock 5m 试点")
    parser.add_argument("codes", nargs="*", default=["600519", "000001", "600000"])
    parser.add_argument("--start", default="2024-01-01", help="BaoStock start_date")
    parser.add_argument("--lookback", type=int, default=120, help="东财 lookback_days")
    args = parser.parse_args()

    from skills.common.baostock_minute import fetch_baostock_minute_bars
    from skills.common.minute_history import fetch_a_minute_bars

    print("=== BaoStock direct ===")
    for code in args.codes:
        bars, meta = fetch_baostock_minute_bars(
            code, period="5", start_date=args.start, adjust="qfq"
        )
        err = meta.get("error")
        print(
            f"{code}: bars={len(bars)} span_days={_span_days(bars)} "
            f"range={meta.get('date_min')}→{meta.get('date_max')} err={err or '—'}"
        )

    print("\n=== EM + BS fallback (force remote) ===")
    for code in args.codes:
        bars, meta = fetch_a_minute_bars(
            code,
            period="5",
            lookback_days=args.lookback,
            use_cache=False,
            max_age_hours=0,
        )
        em = meta.get("em") or {}
        bs = meta.get("baostock") or {}
        print(
            f"{code}: bars={len(bars)} span_days={_span_days(bars)} "
            f"src={meta.get('data_source')} em_err={meta.get('em_error') or '—'} "
            f"bs_reason={bs.get('backfill_reason') or '—'} "
            f"range={meta.get('date_min')}→{meta.get('date_max')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
