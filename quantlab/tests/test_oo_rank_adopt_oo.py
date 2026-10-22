"""ŷ_oo_rank 默认口径复用 ŷ_oo 的 L1，指纹按抬窗后的窗口。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

import numpy as np


def _bars(n: int = 20) -> list:
    out = []
    for i in range(n):
        px = 10.0 + i * 0.1
        out.append(
            {
                "date": f"2024-01-{(i % 28) + 1:02d}",
                "open": px,
                "high": px + 0.2,
                "low": px - 0.2,
                "close": px + 0.05,
                "volume": 1000 + i,
            }
        )
    return out

def _job(bars, **overrides):
    fields = {
        "code": "000001",
        "bars": bars,
        "horizon_days": 1,
        "min_history": 12,
        "max_window": 30,
        "index_bars": None,
        "fundamentals": None,
        "excess_mode": "none",
        "respect_regime": False,
        "config": None,
    }
    fields.update(overrides)
    return (
        fields["code"],
        fields["bars"],
        fields["horizon_days"],
        fields["min_history"],
        fields["max_window"],
        fields["index_bars"],
        fields["fundamentals"],
        fields["excess_mode"],
        fields["respect_regime"],
        fields["config"],
    )

class OoRankAdoptOoTests(unittest.TestCase):
    def test_window_at_or_below_bump_shares_fingerprint(self) -> None:
        from core.research.oo_rank_panel import (
            _can_adopt_oo_panel,
            _effective_oo_collect_window,
            _oo_rank_panel_fingerprint,
        )

        bars = _bars()
        oo_mh, oo_mw = _effective_oo_collect_window(
            12, 30, respect_regime=False, index_bars=None, config=None
        )
        self.assertGreaterEqual(oo_mw, 62)
        fp_default, eff_mh, eff_mw, excess = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=12,
            max_window=30,
            excess_mode="none",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        fp_mid, _, _, _ = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=40,
            max_window=50,
            excess_mode="none",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        self.assertEqual(excess, "none")
        self.assertEqual((eff_mh, eff_mw), (oo_mh, oo_mw))
        self.assertEqual(fp_default, fp_mid)
        self.assertTrue(
            _can_adopt_oo_panel(
                eff_mh=eff_mh,
                eff_mw=eff_mw,
                excess_mode=excess,
                respect_regime=False,
                index_bars=None,
                fundamentals=None,
            )
        )

    def test_longer_window_and_excess_stay_distinct(self) -> None:
        from core.research.oo_rank_panel import (
            _can_adopt_oo_panel,
            _normalize_excess_mode,
            _oo_rank_panel_fingerprint,
        )

        bars = _bars()
        fp_default, _, _, _ = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=12,
            max_window=30,
            excess_mode="none",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        fp_long, mh, mw, _ = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=70,
            max_window=70,
            excess_mode="none",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        fp_excess, _, _, excess = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=12,
            max_window=30,
            excess_mode="excess",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        self.assertEqual((mh, mw), (70, 70))
        self.assertEqual(_normalize_excess_mode("excess"), "index")
        self.assertEqual(excess, "index")
        self.assertNotEqual(fp_long, fp_default)
        self.assertNotEqual(fp_excess, fp_default)
        self.assertFalse(
            _can_adopt_oo_panel(
                eff_mh=mh,
                eff_mw=mw,
                excess_mode="none",
                respect_regime=False,
                index_bars=None,
                fundamentals=None,
            )
        )
        self.assertFalse(
            _can_adopt_oo_panel(
                eff_mh=62,
                eff_mw=62,
                excess_mode="index",
                respect_regime=False,
                index_bars=None,
                fundamentals=None,
            )
        )

    def test_stock_job_copies_oo_matrix(self) -> None:
        from core.research.oo_rank_panel import (
            _oo_rank_panel_fingerprint,
            _oo_rank_stock_job,
        )
        from core.research.panel_fingerprint import bars_fingerprint, make_fingerprint

        bars = _bars()
        x = np.ones((3, 2), dtype=np.float64)
        y = np.array([0.1, 0.2, 0.3], dtype=np.float64)
        names = ["z_oo_a", "raw_aoo_b"]
        dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
        rank_fp, _, _, _ = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=12,
            max_window=30,
            excess_mode="none",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        oo_fp = make_fingerprint(
            bars_fp=bars_fingerprint(bars),
            horizon=1,
            ridge_core_only=0,
        )
        seen = []

        def _load(family, code, fp, **_kwargs):
            seen.append((family, code, fp))
            if family == "oo_rank":
                return None
            if family == "oo":
                self.assertEqual(fp, oo_fp)
                return x, y, names, dates, "l1"
            raise AssertionError(family)

        synced = {}

        def _sync(family, code, **kwargs):
            synced["family"] = family
            synced["code"] = code
            synced["fingerprint"] = kwargs.get("fingerprint")
            return True

        with mock.patch(
            "core.research.lake_bridge.load_training_matrix", side_effect=_load
        ), mock.patch(
            "core.research.lake_bridge.sync_panel_to_lake", side_effect=_sync
        ), mock.patch(
            "core.research.oo_panel.collect_oo_panel",
            side_effect=AssertionError("should adopt oo"),
        ):
            out = _oo_rank_stock_job(_job(bars))

        self.assertIsNotNone(out)
        code, got_x, got_y, got_names, got_dates = out
        self.assertEqual(code, "000001")
        self.assertEqual(got_x.shape, (3, 2))
        self.assertEqual(got_names, names)
        self.assertEqual(got_dates, dates)
        self.assertTrue(np.allclose(got_y, y))
        self.assertEqual([item[0] for item in seen], ["oo_rank", "oo"])
        self.assertEqual(synced["family"], "oo_rank")
        self.assertEqual(synced["code"], "000001")
        self.assertEqual(synced["fingerprint"], rank_fp)

    def test_materialize_writes_oo_before_oo_rank(self) -> None:
        from core.research.materialize import l1_jobs

        order = []

        def _tc(codes, **_kwargs):
            order.append("tc")
            return {"ok": True, "family": "tc"}

        def _oo(codes, **_kwargs):
            order.append("oo")
            return {"ok": True, "family": "oo"}

        def _rank(codes, **_kwargs):
            order.append("oo_rank")
            return {"ok": True, "family": "oo_rank"}

        with mock.patch.object(l1_jobs, "_materialize_tc", _tc), mock.patch.object(
            l1_jobs, "_materialize_oo", _oo
        ), mock.patch.object(l1_jobs, "_materialize_oo_rank", _rank):
            report = l1_jobs.materialize_l1(
                ["oo_rank", "tc", "oo"],
                ["000001", "000002"],
            )
        self.assertEqual(order, ["tc", "oo", "oo_rank"])
        self.assertEqual(report["families"], ["tc", "oo", "oo_rank"])

    def test_consumer_miss_is_not_dropped(self) -> None:
        from core.research.materialize.consumer import (
            MaterializeRequired,
            materialized_consumer,
        )
        from core.research.oo_rank_panel import (
            _oo_rank_min_bars,
            build_oo_rank_day_panels,
        )

        bars = _bars(_oo_rank_min_bars(1))
        packs = [{"code": "000001", "bars": bars}, {"code": "000002", "bars": bars}]
        with materialized_consumer(), mock.patch(
            "core.research.materialize.coverage.enforce_consumer_coverage"
        ), mock.patch(
            "core.research.lake_bridge.load_training_matrix", return_value=None
        ), self.assertRaises(MaterializeRequired):
            build_oo_rank_day_panels(packs, workers=1)

    def test_materialize_passes_horizon_to_oo_rank(self) -> None:
        from core.research.materialize import l1_jobs

        seen = []

        def _rank(codes, **kwargs):
            seen.append(kwargs.get("horizon_days"))
            return {"ok": True, "family": "oo_rank"}

        with mock.patch.object(
            l1_jobs, "_materialize_oo", return_value={"ok": True, "family": "oo"}
        ), mock.patch.object(l1_jobs, "_materialize_oo_rank", _rank):
            report = l1_jobs.materialize_l1(
                ["oo"], ["000001", "000002"], horizon_days=4
            )
        self.assertEqual(seen, [4])
        self.assertEqual(report["families"], ["oo", "oo_rank"])

    def test_one_family_exception_does_not_drop_the_report(self) -> None:
        from core.research.materialize import l1_jobs

        with mock.patch.object(
            l1_jobs, "_materialize_oo", side_effect=RuntimeError("oo boom")
        ), mock.patch.object(
            l1_jobs,
            "_materialize_tc",
            return_value={"ok": True, "family": "tc"},
        ):
            report = l1_jobs.materialize_l1(
                ["tc", "oo"], ["000001", "000002"]
            )
        self.assertFalse(report["ok"])
        self.assertIn("oo boom", report["by_family"]["oo"]["error"])
        self.assertTrue(report["by_family"]["tc"]["ok"])
        self.assertNotIn("oo_rank", report["by_family"])

    def test_other_spec_does_not_tail_merge_default_matrix(self) -> None:
        from core.research.feature_lake import write_stock_panel
        from core.research.incremental_panel import stem_extra_meta
        from core.research.lake_bridge import FAMILY_OO_RANK
        from core.research.oo_rank_panel import (
            _oo_rank_min_bars,
            _oo_rank_panel_fingerprint,
            _oo_rank_stock_job,
        )

        bars = _bars(_oo_rank_min_bars(1))
        default_fp, _, _, _ = _oo_rank_panel_fingerprint(
            bars,
            horizon_days=1,
            min_history=12,
            max_window=30,
            excess_mode="none",
            respect_regime=False,
            index_bars=None,
            config=None,
        )
        tmp = tempfile.TemporaryDirectory()
        prev = {
            "QUANTLAB_FEATURE_LAKE_DIR": os.environ.get("QUANTLAB_FEATURE_LAKE_DIR"),
            "QUANTLAB_FEATURE_LAKE": os.environ.get("QUANTLAB_FEATURE_LAKE"),
            "QUANTLAB_DAY_CUBE_GC": os.environ.get("QUANTLAB_DAY_CUBE_GC"),
        }
        os.environ["QUANTLAB_FEATURE_LAKE_DIR"] = tmp.name
        os.environ["QUANTLAB_FEATURE_LAKE"] = "1"
        os.environ["QUANTLAB_DAY_CUBE_GC"] = "0"
        calls = []

        def _collect(*_args, **kwargs):
            calls.append(kwargs.get("keep_dates"))
            raise RuntimeError("stop after observing collect")

        try:
            write_stock_panel(
                FAMILY_OO_RANK,
                "000001",
                dates=["2024-01-01", "2024-01-02"],
                X=np.ones((2, 1), dtype=np.float64),
                names=["z_oo_a"],
                fingerprint=default_fp,
                y=np.asarray([0.01, 0.02]),
                extra_meta=stem_extra_meta(bars),
            )
            with mock.patch(
                "core.research.oo_panel.collect_oo_panel", side_effect=_collect
            ), self.assertRaises(RuntimeError):
                _oo_rank_stock_job(_job(bars, excess_mode="index"))
        finally:
            for key, val in prev.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val
            tmp.cleanup()
        self.assertEqual(calls, [None])

    def test_short_bars_skip_like_oo_without_materialize(self) -> None:
        """次新日线 < ALPHA_OO_PANEL_WINDOW+h：与 ŷ_oo 一样跳过，不报物化。"""
        from core.research.oo_rank_panel import _oo_rank_min_bars, _oo_rank_stock_job
        from core.signal.factors.alpha_oo import ALPHA_OO_PANEL_WINDOW

        need = _oo_rank_min_bars(1)
        self.assertEqual(need, int(ALPHA_OO_PANEL_WINDOW) + 1)
        short = _bars(need - 1)
        with mock.patch(
            "core.research.materialize.consumer.raise_if_consumer_rebuild"
        ) as raise_rebuild:
            out = _oo_rank_stock_job(_job(short, min_history=12, max_window=30))
        self.assertIsNone(out)
        raise_rebuild.assert_not_called()
