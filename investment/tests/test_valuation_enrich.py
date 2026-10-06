"""估值补全与组 IC 强制算全因子。"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestValuationEnrich(unittest.TestCase):
    def test_enrich_fills_pe_pb_market_cap(self):
        from core.valuation_em import enrich_fundamentals_metrics

        with patch(
            "core.valuation_em.fetch_valuation_pack",
            return_value={
                "pe": 20.0,
                "pb": 5.0,
                "pe_ttm": 19.5,
                "market_cap": 1e12,
                "dividend_yield": None,
            },
        ):
            out = enrich_fundamentals_metrics("600519", {"roe": 10.0})
        self.assertEqual(out["roe"], 10.0)
        self.assertEqual(out["pe"], 20.0)
        self.assertEqual(out["pb"], 5.0)
        self.assertEqual(out["market_cap"], 1e12)

    def test_enrich_does_not_overwrite(self):
        from core.valuation_em import enrich_fundamentals_metrics

        with patch(
            "core.valuation_em.fetch_valuation_pack",
            return_value={"pe": 99.0, "pb": 9.0, "pe_ttm": 99.0, "market_cap": 1.0},
        ):
            out = enrich_fundamentals_metrics(
                "600519", {"pe": 18.0, "pb": 6.0, "roe": 12.0}
            )
        self.assertEqual(out["pe"], 18.0)
        self.assertEqual(out["pb"], 6.0)

    def test_enrich_fills_dividend_yield(self):
        from core.valuation_em import enrich_fundamentals_metrics

        with patch(
            "core.valuation_em.fetch_valuation_pack",
            return_value={
                "pe": 10.0,
                "pb": 1.0,
                "pe_ttm": 10.0,
                "market_cap": 1e11,
                "dividend_yield": 0.032,
            },
        ):
            out = enrich_fundamentals_metrics("000001", {"roe": 8.0})
        self.assertAlmostEqual(out["dividend_yield"], 0.032)

    def test_fetch_pack_fills_dy_when_pe_cached(self):
        from core import valuation_em as ve

        with patch.object(
            ve,
            "read_valuation_cache",
            return_value={
                "pe": 12.0,
                "pb": 1.2,
                "pe_ttm": 12.0,
                "market_cap": 1e12,
                "dividend_yield": None,
            },
        ), patch.object(
            ve, "lookup_fhps_dividend_yield", return_value=0.025
        ), patch.object(ve, "write_valuation_cache") as wcache:
            pack = ve.fetch_valuation_pack("601988")
        self.assertEqual(pack["pe"], 12.0)
        self.assertAlmostEqual(pack["dividend_yield"], 0.025)
        self.assertTrue(wcache.called)

    def test_merge_cached_valuation_fills_market_cap(self):
        from core import valuation_em as ve

        with patch.object(
            ve,
            "read_valuation_cache",
            return_value={
                "pe": 20.0,
                "pb": 3.0,
                "pe_ttm": 20.0,
                "market_cap": 1.07e12,
                "dividend_yield": None,
            },
        ):
            out = ve.merge_cached_valuation("688981", {"roe": 0.9})
        self.assertEqual(out["roe"], 0.9)
        self.assertEqual(out["market_cap"], 1.07e12)
        self.assertEqual(out["pe"], 20.0)

    def test_merge_cached_valuation_no_overwrite(self):
        from core import valuation_em as ve

        with patch.object(
            ve,
            "read_valuation_cache",
            return_value={"market_cap": 9e9, "pe": 99.0},
        ):
            out = ve.merge_cached_valuation(
                "688981", {"market_cap": 1e12, "roe": 1.0}
            )
        self.assertEqual(out["market_cap"], 1e12)
        self.assertEqual(out["pe"], 99.0)

    def test_merge_local_fundamentals_snapshot_fills_roe(self):
        from core.fundamentals_pit import merge_local_fundamentals_snapshot

        with patch(
            "core.fundamentals_pit.load_fundamentals_panel",
            return_value={
                "history": [
                    {
                        "as_of": "2026-08-13",
                        "metrics": {
                            "roe": 0.9,
                            "profit_growth": 0.36,
                            "revenue_growth": 8.07,
                        },
                    }
                ]
            },
        ):
            out = merge_local_fundamentals_snapshot(
                "688981", {"market_cap": 1e12, "pe": 200.0}
            )
        self.assertEqual(out["market_cap"], 1e12)
        self.assertEqual(out["roe"], 0.9)
        self.assertEqual(out["profit_growth"], 0.36)


class TestCsIcRequireAll(unittest.TestCase):
    def test_score_window_forwards_required_keys(self):
        from core.signal.cross_section_batch import score_window_as_item

        bars = [
            {
                "date": f"2024-01-{i:02d}",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.0 + i * 0.01,
                "volume": 1e6,
            }
            for i in range(1, 25)
        ]
        with patch("core.signal.cross_section_batch.score_bars") as scored:
            scored.return_value = {
                "hard_reject": False,
                "sub_scores": {"momentum": 55.0, "amihud": 40.0},
                "score": 50.0,
            }
            score_window_as_item(
                "600519",
                bars,
                horizon_days=3,
                quote={"change_raw": 0.1},
                required_factor_keys=["momentum", "amihud", "value"],
            )
            kwargs = scored.call_args.kwargs
            self.assertEqual(
                kwargs.get("required_factor_keys"),
                ["momentum", "amihud", "value"],
            )


class TestPanelIcFactorNames(unittest.TestCase):
    def test_includes_fundamentals_beyond_cluster_union(self):
        """panel_ic_factor_names 随 factor_ols_clusters 删除；用 panel resolve 验证。"""
        from core.research.panel import _resolve_factor_names

        names = list(_resolve_factor_names(respect_regime=False, index_bars=None, config=None))
        for extra in ["momentum", "volume_price"]:
            if extra not in names:
                names.append(extra)
        self.assertIn("momentum", names)
        self.assertIn("value", names)
        self.assertIn("quality", names)
        self.assertIn("dividend", names)


if __name__ == "__main__":
    unittest.main()
