"""一次性观察池日线 topup（收盘后补齐）。用法：python3 scripts/run_bars_topup_once.py"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("QUANTLAB_BARS_BACKEND", "sqlite")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> int:
    from core.paths import BARS_FORCED_SESSION_PATH
    from core.watching.store import WATCHING_MAX_SIZE
    from quant.research.bars_status import build_bars_status, refresh_bars_only

    # 清掉盘中误写的「今日已刷」标记，避免失败后 UI 仍显示已完成
    try:
        if os.path.isfile(BARS_FORCED_SESSION_PATH):
            os.remove(BARS_FORCED_SESSION_PATH)
            print("cleared forced marker", BARS_FORCED_SESSION_PATH, flush=True)
    except OSError as e:
        print("clear marker failed", e, flush=True)

    def progress(msg: str, cur: int = 0, tot: int = 0) -> None:
        print(
            f"[{datetime.now().isoformat(timespec='seconds')}] {msg} ({cur}/{tot})",
            flush=True,
        )

    print("START topup", datetime.now().isoformat(timespec="seconds"), flush=True)
    t0 = time.time()
    out = refresh_bars_only(
        watching_limit=WATCHING_MAX_SIZE,
        mode="topup",
        progress_cb=progress,
    )
    elapsed = time.time() - t0
    print("DONE elapsed_sec", round(elapsed, 1), flush=True)
    br = out.get("bars_refresh") or {}
    print(
        "bars_refresh",
        {
            k: br.get(k)
            for k in (
                "mode",
                "remote_count",
                "failed_count",
                "cache_count",
                "gap_count",
                "full_count",
                "tail_count",
                "total",
                "fetch_limit",
                "note",
            )
        },
        flush=True,
    )
    st = out.get("status") or build_bars_status(watching_limit=WATCHING_MAX_SIZE)
    print(
        "status",
        {
            k: st.get(k)
            for k in (
                "expected_latest_bar",
                "at_expected",
                "stale",
                "missing",
                "coverage_ok",
                "coverage_pct",
                "needs_force_latest_bars",
                "universe_count",
            )
        },
        flush=True,
    )
    print("success", out.get("success"), flush=True)
    failed = int(br.get("failed_count") or 0)
    return 0 if out.get("success") and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
