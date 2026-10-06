"""offline 打分不得复用 live 拉回的指数缓存（相对强弱 z 会被污染）。"""

from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestLiveIndexCacheIsolation(unittest.TestCase):
    def test_offline_skips_live_poisoned_cache(self):
        from core.signal import live_features as lf
        from core.ports.market import default_benchmark

        bench = str(default_benchmark("CN") or "").strip() or "sh000300"
        fake = [
            {
                "date": f"2026-08-{d:02d}",
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0 + d,
            }
            for d in range(1, 40)
        ]
        with lf._INDEX_LOCK:
            lf._INDEX_CACHE[bench] = (time.time(), fake, "poison_live", "live")

        pack = lf.fetch_live_index_bars(market="CN", offline_only=True)
        self.assertFalse(pack.get("ok"))
        self.assertEqual(pack.get("reason"), "offline_only_miss")
        self.assertEqual(len(pack.get("bars") or []), 0)

        # live 仍可读到毒缓存
        pack_live = lf.fetch_live_index_bars(market="CN", offline_only=False)
        self.assertTrue(pack_live.get("cached"))
        self.assertEqual(len(pack_live.get("bars") or []), len(fake))

        # 做 T 回测路径：peek 可用 live 缓存，但不触发新拉网
        peek = lf.peek_cached_index_bars(market="CN", allow_live_origin=True)
        self.assertTrue(peek.get("ok"))
        self.assertEqual(len(peek.get("bars") or []), len(fake))
        peek_off = lf.peek_cached_index_bars(market="CN", allow_live_origin=False)
        self.assertFalse(peek_off.get("ok"))
        self.assertEqual(len(peek_off.get("bars") or []), 0)


if __name__ == "__main__":
    unittest.main()
