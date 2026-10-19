"""面板指纹工具；训练写穿见 Feature Lake。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest import mock

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


class TestPanelFingerprint(unittest.TestCase):
    def test_bars_fingerprint_changes_on_new_day(self) -> None:
        from core.research.panel_fingerprint import bars_fingerprint

        a = _bars(5)
        b = list(a)
        b.append(
            {
                "date": "2024-02-10",
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 100,
            }
        )
        self.assertNotEqual(bars_fingerprint(a), bars_fingerprint(b))
        self.assertEqual(bars_fingerprint(a), bars_fingerprint(list(a)))

    def test_mid_bar_change_invalidates_fingerprint(self) -> None:
        from core.research.panel_fingerprint import PANEL_CACHE_SCHEMA, bars_fingerprint

        a = _bars(10)
        b = [dict(x) for x in a]
        b[3] = dict(b[3])
        b[3]["close"] = float(b[3]["close"]) + 9.0
        self.assertNotEqual(bars_fingerprint(a), bars_fingerprint(b))
        self.assertEqual(PANEL_CACHE_SCHEMA, 3)

    def test_head_keys_stable(self) -> None:
        from core.research.panel_fingerprint import (
            co_head_key,
            make_fingerprint,
            oo_head_key,
            tc_head_key,
        )

        self.assertEqual(
            oo_head_key(horizon_days=1, ridge_core_only=False),
            oo_head_key(horizon_days=1, ridge_core_only=False),
        )
        self.assertNotEqual(
            oo_head_key(horizon_days=1, ridge_core_only=False),
            oo_head_key(horizon_days=1, ridge_core_only=True),
        )
        self.assertIn("tc", tc_head_key(tau_hm="open", min_history=12, include_alpha_tc=False, use_minute=False))
        self.assertIn("co", co_head_key(min_history=12, include_alpha_oo=True))
        a = make_fingerprint(bars_fp="z", horizon=1, ridge_core_only=0)
        b = make_fingerprint(bars_fp="z", ridge_core_only=0, horizon=1)
        self.assertEqual(a, b)


class TestAssembleUsesL1(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = os.path.join(self._tmpdir.name, "l1")
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"

    def tearDown(self) -> None:
        for k, v in self._prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmpdir.cleanup()

    def test_assemble_oo_panels_cache_hit(self) -> None:
        from core.research.feature_lake import load_stock_panel
        from core.research.lake_bridge import FAMILY_OO
        from core.research.oo_ridge_compact import assemble_oo_panels
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
        from core.research.materialize.consumer import materialize_writer

        items = [
            ("AAA", _bars(80, 10.0, 0)),
            ("BBB", _bars(80, 12.0, 1)),
        ]
        with materialize_writer():
            x1, y1, names1, d1 = assemble_oo_panels(items, horizon_days=1, workers=1)
            self.assertGreater(int(y1.shape[0]), 0)
            fp_a = make_fingerprint(
                bars_fp=bars_fingerprint(items[0][1]),
                horizon=1,
                ridge_core_only=0,
            )
            self.assertIsNotNone(load_stock_panel(FAMILY_OO, "AAA", fingerprint=fp_a))

            x2, y2, names2, d2 = assemble_oo_panels(items, horizon_days=1, workers=1)
            np.testing.assert_allclose(x1, x2)
            np.testing.assert_allclose(y1, y2)
            self.assertEqual(names1, names2)
            self.assertEqual(d1, d2)

            mutated = [dict(b) for b in items[0][1]]
            mutated[-1] = dict(mutated[-1])
            mutated[-1]["close"] = float(mutated[-1]["close"]) * 1.05
            items2 = [("AAA", mutated), ("BBB", items[1][1])]
            x3, y3, _n3, _d3 = assemble_oo_panels(items2, horizon_days=1, workers=1)
            self.assertEqual(y3.shape[0], y1.shape[0])
            self.assertFalse(np.allclose(x1, x3))

    def test_tau_and_rank_l1_families(self) -> None:
        from core.research.feature_lake import invalidate_code, load_stock_panel
        from core.research.lake_bridge import FAMILY_TC, FAMILY_TC_RANK
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint
        from core.research.panel_matrix import (
            _breadth_keys,
            _tau_stock_job,
            _tc_matrix_seed_names,
        )
        from core.research.tc_ridge import TAU_Z_FEATURES
        from core.research.materialize.consumer import materialize_writer

        bars = _bars(80, 10.0, 2)
        col_names = _tc_matrix_seed_names(list(TAU_Z_FEATURES), include_alpha_tc=False)
        slim_keys = _breadth_keys()
        kw = dict(
            use_minute=False,
            tau_hm="open",
            grid=None,
            min_history=12,
            include_alpha_tc=False,
        )
        with materialize_writer():
            pack_tc = _tau_stock_job(
                {"code": "RANK1", "bars": list(bars)}, col_names, slim_keys, **kw
            )
            self.assertIsNotNone(pack_tc)
            assert pack_tc is not None
            fp = make_fingerprint(
                bars_fp=bars_fingerprint(bars),
                tau_hm="open",
                min_history=12,
                include_alpha_tc=0,
                include_alpha_th=0,
                use_minute=0,
            )
            self.assertIsNotNone(load_stock_panel(FAMILY_TC, "RANK1", fingerprint=fp))

            with mock.patch(
                "core.research.tau_panel.collect_tau_open_panel",
                side_effect=AssertionError("tc_rank should copy tc"),
            ):
                pack_rank = _tau_stock_job(
                    {"code": "RANK1", "bars": list(bars)},
                    col_names,
                    slim_keys,
                    cache_head="tc_rank",
                    **kw,
                )
            self.assertIsNotNone(pack_rank)
            assert pack_rank is not None
            np.testing.assert_allclose(pack_tc[1], pack_rank[1])
            self.assertIsNotNone(load_stock_panel(FAMILY_TC_RANK, "RANK1", fingerprint=fp))

            invalidate_code(FAMILY_TC, "RANK1")
            pack_rank2 = _tau_stock_job(
                {"code": "RANK1", "bars": list(bars)},
                col_names,
                slim_keys,
                cache_head="tc_rank",
                **kw,
            )
            self.assertIsNotNone(pack_rank2)
            assert pack_rank2 is not None
            np.testing.assert_allclose(pack_rank[1], pack_rank2[1])


if __name__ == "__main__":
    unittest.main()
