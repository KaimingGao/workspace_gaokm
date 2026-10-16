"""组合回测日线批量离线加载：与逐票路径尾窗一致。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.research.portfolio_bars import (
    _load_bars_parallel,
    _load_offline_bars_from_store,
)
from core.store import save_daily_cache
from core.store_bars_sqlite import reset_conn_cache


def _bars(n: int, *, start_offset: int = 0):
    out = []
    start = datetime(2024, 1, 1) + timedelta(days=start_offset)
    for i in range(n):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append(
            {
                "date": d,
                "open": 10 + i * 0.1,
                "high": 10.5 + i * 0.1,
                "low": 9.8 + i * 0.1,
                "close": 10.2 + i * 0.1,
                "volume": 1000 + i,
            }
        )
    return out


class TestPortfolioBarsBatchOffline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        reset_conn_cache()
        self._env = patch.dict(
            os.environ,
            {
                "QUANTLAB_STORE_DIR": self.tmp,
                "QUANTLAB_BARS_BACKEND": "sqlite",
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(reset_conn_cache)
        self.codes = ["000001", "600519", "300750"]
        for i, code in enumerate(self.codes):
            save_daily_cache(
                "CN",
                code,
                _bars(80, start_offset=i),
                data_source="akshare_cn_daily",
                store_dir=self.tmp,
            )

    def test_offline_batch_tail_matches_per_code_service(self):
        limit = 40
        batch = _load_offline_bars_from_store(self.codes, limit=limit)
        self.assertEqual(set(batch.keys()), set(self.codes))
        for code in self.codes:
            sym, bars = batch[code]
            self.assertEqual(sym, code)
            self.assertEqual(len(bars), limit)
            from core.research.portfolio_bars import _fetch_bars_for_raw

            _raw, _sym, one = _fetch_bars_for_raw(
                code, limit=limit, offline_ok=True, offline_only=True
            )
            self.assertEqual(
                [b["date"] for b in bars],
                [b["date"] for b in one],
            )

    def test_load_bars_parallel_offline_only_uses_store(self):
        by_raw, remote_n = _load_bars_parallel(
            self.codes + ["999998"],
            limit=30,
            need=16,
            offline_ok=True,
            remote_fill=False,
        )
        self.assertEqual(remote_n, 0)
        self.assertEqual(len(by_raw["000001"][1]), 30)
        self.assertEqual(by_raw["999998"][1], [])


if __name__ == "__main__":
    unittest.main()
