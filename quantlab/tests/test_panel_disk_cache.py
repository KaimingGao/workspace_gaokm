"""按股 panel 磁盘缓存：fingerprint 命中 / 失效。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _bars(n: int = 80, start: float = 10.0, seed: int = 0):
    px = start
    out = []
    for i in range(n):
        o = px * (1.0 + (0.01 if (i + seed) % 5 == 0 else 0.0))
        c = o * (1.0 + ((i % 7) - 3) * 0.002)
        out.append(
            {
                "date": f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "open": round(o, 4),
                "high": round(max(o, c) * 1.01, 4),
                "low": round(min(o, c) * 0.99, 4),
                "close": round(c, 4),
                "volume": 1_000_000 + i * 1000,
            }
        )
        px = c
    return out


class TestPanelDiskCache(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev_dir = os.environ.get("QUANTLAB_PANEL_CACHE_DIR")
        self._prev_en = os.environ.get("QUANTLAB_PANEL_CACHE")
        os.environ["QUANTLAB_PANEL_CACHE_DIR"] = self._tmpdir.name
        os.environ["QUANTLAB_PANEL_CACHE"] = "1"

    def tearDown(self) -> None:
        if self._prev_dir is None:
            os.environ.pop("QUANTLAB_PANEL_CACHE_DIR", None)
        else:
            os.environ["QUANTLAB_PANEL_CACHE_DIR"] = self._prev_dir
        if self._prev_en is None:
            os.environ.pop("QUANTLAB_PANEL_CACHE", None)
        else:
            os.environ["QUANTLAB_PANEL_CACHE"] = self._prev_en
        self._tmpdir.cleanup()

    def test_bars_fingerprint_changes_on_new_day(self) -> None:
        from core.research.panel_disk_cache import bars_fingerprint

        a = _bars(40, seed=1)
        b = list(a)
        b.append(
            {
                "date": "2024-03-01",
                "open": 11.0,
                "high": 11.2,
                "low": 10.8,
                "close": 11.1,
                "volume": 2_000_000,
            }
        )
        self.assertNotEqual(bars_fingerprint(a), bars_fingerprint(b))

    def test_save_load_roundtrip_and_miss(self) -> None:
        from core.research.panel_disk_cache import (
            load_stock_shard,
            make_fingerprint,
            oo_head_key,
            save_stock_shard,
        )

        head = oo_head_key(horizon_days=1, ridge_core_only=False)
        fp = make_fingerprint(bars_fp="abc", horizon=1, ridge_core_only=0)
        X = np.arange(12, dtype=np.float64).reshape(3, 4)
        y = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
        names = ["a", "b", "c", "d"]
        save_stock_shard(head, "600000.SH", fp, X=X, y=y, names=names, dates=dates)
        hit = load_stock_shard(head, "600000.SH", fp)
        self.assertIsNotNone(hit)
        assert hit is not None
        np.testing.assert_allclose(hit["X"], X)
        np.testing.assert_allclose(hit["y"], y)
        self.assertEqual(hit["names"], names)
        self.assertEqual(hit["dates"], dates)
        miss = load_stock_shard(head, "600000.SH", fp + "x")
        self.assertIsNone(miss)

    def test_assemble_oo_panels_cache_hit(self) -> None:
        from core.research import panel_disk_cache as pdc
        from core.research.oo_ridge_compact import assemble_oo_panels

        items = [
            ("AAA", _bars(80, 10.0, 0)),
            ("BBB", _bars(80, 12.0, 1)),
        ]
        x1, y1, names1, d1 = assemble_oo_panels(
            items, horizon_days=1, workers=1
        )
        self.assertGreater(int(y1.shape[0]), 0)
        head = pdc.oo_head_key(horizon_days=1, ridge_core_only=False)
        fp_a = pdc.make_fingerprint(
            bars_fp=pdc.bars_fingerprint(items[0][1]),
            horizon=1,
            ridge_core_only=0,
        )
        self.assertIsNotNone(pdc.load_stock_shard(head, "AAA", fp_a))

        x2, y2, names2, d2 = assemble_oo_panels(
            items, horizon_days=1, workers=1
        )
        np.testing.assert_allclose(x1, x2)
        np.testing.assert_allclose(y1, y2)
        self.assertEqual(names1, names2)
        self.assertEqual(d1, d2)

        # 末 bar 变 → fingerprint 变 → 重算仍可得到合法面板
        mutated = [dict(b) for b in items[0][1]]
        mutated[-1] = dict(mutated[-1])
        mutated[-1]["close"] = float(mutated[-1]["close"]) * 1.05
        items2 = [("AAA", mutated), ("BBB", items[1][1])]
        x3, y3, _n3, _d3 = assemble_oo_panels(
            items2, horizon_days=1, workers=1
        )
        self.assertEqual(y3.shape[0], y1.shape[0])
        self.assertFalse(np.allclose(x1, x3))

    def test_disabled_skips_disk(self) -> None:
        from core.research.panel_disk_cache import (
            enabled,
            load_stock_shard,
            make_fingerprint,
            oo_head_key,
            save_stock_shard,
        )

        os.environ["QUANTLAB_PANEL_CACHE"] = "0"
        self.assertFalse(enabled())
        head = oo_head_key(horizon_days=1, ridge_core_only=False)
        fp = make_fingerprint(bars_fp="z", horizon=1, ridge_core_only=0)
        save_stock_shard(
            head,
            "CCC",
            fp,
            X=np.ones((2, 2)),
            y=np.ones(2),
            names=["a", "b"],
            dates=["2024-01-01", "2024-01-02"],
        )
        self.assertIsNone(load_stock_shard(head, "CCC", fp))


if __name__ == "__main__":
    unittest.main()
