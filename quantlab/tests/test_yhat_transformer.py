"""yhat_transformer：HeadSpec + PIT + 前向烟雾。"""

from __future__ import annotations

import unittest

import numpy as np


class TestHeadSpec(unittest.TestCase):
    def test_four_heads_registered(self):
        from core.research.yhat_transformer.heads import get_head, head_ids, iter_heads

        self.assertEqual(head_ids(), ["oo", "tc", "oo_rank", "tc_rank"])
        oo = get_head("oo")
        self.assertEqual(oo.task_type, "regression")
        self.assertEqual(oo.status, "holdout_ready")
        self.assertFalse(hasattr(oo, "default_cli"))
        tc = get_head("tc")
        self.assertEqual(tc.status, "holdout_ready")
        self.assertEqual(tc.factor_pack, "atc_full")
        self.assertEqual(oo.factor_pack, "aoo_full")
        oo_r = get_head("oo_rank")
        self.assertEqual(oo_r.task_type, "rank")
        self.assertEqual(oo_r.status, "holdout_ready")
        self.assertEqual(oo_r.loss, "lambdarank")
        tc_r = get_head("tc_rank")
        self.assertEqual(tc_r.status, "holdout_ready")
        from core.research.yhat_transformer.heads import assert_trainable
        from core.research.yhat_tf_desk import build_holdout_cli

        self.assertNotIn("--atc-core", build_holdout_cli("tc"))
        self.assertIn("--atc-core", build_holdout_cli("tc", {"full_atc": False}))
        for hid in head_ids():
            assert_trainable(get_head(hid))
            cli = build_holdout_cli(hid)
            self.assertIn("run_yhat_transformer_holdout", cli)
            self.assertIn(f"--head {hid}", cli)
        labels = [h.label for h in iter_heads()]
        self.assertEqual(len(labels), 4)

    def test_align_seq_and_factor_overwrites_fac_y(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            align_seq_and_factor,
        )
        from core.research.yhat_transformer.seq_dataset import SeqPanel

        n, L, F = 4, 3, 8
        dates = ["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03"]
        codes = ["000001", "000001", "000002", "000002"]
        seq = SeqPanel(
            X=np.zeros((n, L, F), dtype=np.float32),
            gap=np.zeros((n,), dtype=np.float32),
            y=np.asarray([1.0, 2.0, 3.0, 4.0], dtype=np.float32),
            dates=dates,
            codes=codes,
            seq_len=L,
        )
        fac = FactorPanel(
            X=np.zeros((n, 2), dtype=np.float32),
            y=np.asarray([1.0, 9.0, 3.0, 8.0], dtype=np.float32),
            dates=dates,
            codes=codes,
            feature_names=["a", "b"],
        )
        sc, fc = align_seq_and_factor(seq, fac)
        self.assertEqual(len(sc.y), 4)
        np.testing.assert_allclose(fc.y, sc.y)

    def test_arm_t_slice_leaves_full_for_l(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            slice_arm_t_panel,
        )
        from core.signal.factors.alpha_tc import (
            ALPHA_TC_FIELD_NAMES,
            ALPHA_TC_RAW_PREFIX,
        )

        names = [
            "gap_pct",
            "open_gap",
            "gap_atr",
            "yclose_loc",
            "tau_lag1",
            "tau_ma5",
        ] + [f"{ALPHA_TC_RAW_PREFIX}{k}" for k in ALPHA_TC_FIELD_NAMES]
        panel = FactorPanel(
            X=np.zeros((4, len(names)), dtype=np.float32),
            y=np.zeros((4,), dtype=np.float32),
            dates=["d"] * 4,
            codes=["c"] * 4,
            feature_names=names,
        )
        full = slice_arm_t_panel(panel, "tc", atc_core_only=False)
        self.assertIs(full, panel)
        self.assertEqual(full.X.shape[1], 230)
        core = slice_arm_t_panel(panel, "tc", atc_core_only=True)
        self.assertEqual(core.X.shape[1], 55)
        self.assertEqual(panel.X.shape[1], 230)
        self.assertFalse(np.shares_memory(core.X, panel.X))
        short = FactorPanel(
            X=np.zeros((2, 3), dtype=np.float32),
            y=np.zeros((2,), dtype=np.float32),
            dates=["d", "d"],
            codes=["c", "c"],
            feature_names=["a", "b", "c"],
        )
        with self.assertRaises(ValueError):
            slice_arm_t_panel(short, "tc", atc_core_only=True)

    def test_tc_open_seq_label(self):
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        stock = {
            "000001": _synth_bars(50, seed=1),
            "000002": _synth_bars(50, seed=2),
        }
        panel = build_seq_panel(
            stock, head="tc", seq_len=8, horizon_days=1, use_cache=False
        )
        self.assertGreater(panel.n, 10)
        # close/open 通常 |y| 小于极端 oo 隔夜；但仍应有限
        self.assertTrue(np.all(np.isfinite(panel.y)))

    def test_rank_heads_reuse_regression_labels(self):
        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        stock = {
            "000001": _synth_bars(50, seed=1),
            "000002": _synth_bars(50, seed=2),
        }
        oo = build_seq_panel(stock, head="oo", seq_len=8, horizon_days=1, use_cache=False)
        oo_r = build_seq_panel(
            stock, head="oo_rank", seq_len=8, horizon_days=1, use_cache=False
        )
        self.assertEqual(oo.n, oo_r.n)
        np.testing.assert_allclose(oo.y, oo_r.y, rtol=0, atol=1e-5)
        tc = build_seq_panel(stock, head="tc", seq_len=8, horizon_days=1, use_cache=False)
        tc_r = build_seq_panel(
            stock, head="tc_rank", seq_len=8, horizon_days=1, use_cache=False
        )
        self.assertEqual(tc.n, tc_r.n)
        np.testing.assert_allclose(tc.y, tc_r.y, rtol=0, atol=1e-5)

def _synth_bars(n: int = 80, seed: int = 0):
    rng = np.random.RandomState(seed)
    bars = []
    px = 10.0
    from datetime import date, timedelta

    d0 = date(2024, 1, 2)
    for i in range(n):
        o = px * (1.0 + rng.normal(0, 0.005))
        c = o * (1.0 + rng.normal(0, 0.01))
        h = max(o, c) * (1.0 + abs(rng.normal(0, 0.004)))
        lo = min(o, c) * (1.0 - abs(rng.normal(0, 0.004)))
        v = float(rng.randint(1_000_000, 5_000_000))
        bars.append(
            {
                "date": (d0 + timedelta(days=i)).isoformat(),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
            }
        )
        px = c
    return bars


class TestYhatSeqPanel(unittest.TestCase):
    def test_few_stock_errors_are_skipped(self):
        from unittest import mock

        from core.research.yhat_transformer import seq_dataset

        stock = {f"{i:06d}": _synth_bars(40, seed=i) for i in range(5)}
        real = seq_dataset.samples_from_bars

        def _boom(code, bars, **kwargs):
            if code == "000000":
                raise RuntimeError("bad bar")
            return real(code, bars, **kwargs)

        with mock.patch.object(seq_dataset, "samples_from_bars", side_effect=_boom):
            panel = seq_dataset.build_seq_panel(
                stock, head="oo", seq_len=8, use_cache=False
            )
        self.assertGreater(panel.n, 0)
        self.assertNotIn("000000", panel.codes)

    def test_too_many_stock_errors_abort(self):
        from unittest import mock

        from core.research.yhat_transformer import seq_dataset

        stock = {f"{i:06d}": [] for i in range(5)}
        with mock.patch.object(
            seq_dataset, "samples_from_bars", side_effect=RuntimeError("bad")
        ):
            with self.assertRaises(RuntimeError):
                seq_dataset.build_seq_panel(stock, head="oo", seq_len=8, use_cache=False)

    def test_seq_panel_cache_skips_rebuild(self):
        import tempfile
        from unittest import mock

        from core.research.yhat_transformer import seq_dataset

        stock = {f"{i:06d}": _synth_bars(40, seed=i) for i in range(3)}
        with tempfile.TemporaryDirectory() as td:
            first = seq_dataset.build_seq_panel(
                stock, head="oo", seq_len=8, cache_dir=td, use_cache=True
            )
            self.assertGreater(first.n, 0)

            def _boom(*_a, **_k):
                raise AssertionError("cache hit should not rebuild")

            with mock.patch.object(seq_dataset, "samples_from_bars", side_effect=_boom):
                second = seq_dataset.build_seq_panel(
                    stock, head="oo", seq_len=8, cache_dir=td, use_cache=True
                )
            self.assertEqual(second.n, first.n)
            np.testing.assert_allclose(second.y, first.y)

            changed = {k: list(v) for k, v in stock.items()}
            changed["000000"][20]["close"] = float(changed["000000"][20]["close"]) + 3.0
            third = seq_dataset.build_seq_panel(
                changed, head="oo", seq_len=8, cache_dir=td, use_cache=True
            )
            self.assertGreater(third.n, 0)
            self.assertFalse(np.allclose(third.X, first.X))

    def test_bars_token_tracks_ohlcv(self):
        from core.research.yhat_transformer.seq_dataset import _bars_token

        bars = _synth_bars(8, seed=1)
        base = _bars_token(bars)
        bars[3]["volume"] = float(bars[3]["volume"]) + 1.0
        self.assertNotEqual(_bars_token(bars), base)
        bars[3]["high"] = float(bars[3]["high"]) + 0.05
        vol_and_high = _bars_token(bars)
        self.assertNotEqual(vol_and_high, base)
        bars[3]["open"] = float(bars[3]["open"]) + 0.05
        self.assertNotEqual(_bars_token(bars), vol_and_high)
        ordered = _synth_bars(8, seed=2)
        self.assertEqual(_bars_token(list(reversed(ordered))), _bars_token(ordered))

    def test_reorder_panel_by_codes_matches_item_order(self):
        from core.research.yhat_transformer.factor_panel import (
            FactorPanel,
            _cache_panel_matches_pool,
            reorder_panel_by_codes,
        )

        # 完成序：B 在前；入池序 A→B 时应重排。
        panel = FactorPanel(
            X=np.asarray([[1.0], [2.0], [3.0], [4.0]], dtype=np.float32),
            y=np.asarray([10.0, 20.0, 30.0, 40.0], dtype=np.float32),
            dates=["2024-01-02", "2024-01-01", "2024-01-02", "2024-01-01"],
            codes=["B", "B", "A", "A"],
            feature_names=["f0"],
        )
        out = reorder_panel_by_codes(panel, ["A", "B"])
        self.assertEqual(out.codes, ["A", "A", "B", "B"])
        self.assertEqual(out.dates, ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"])
        np.testing.assert_allclose(out.y, [40.0, 30.0, 20.0, 10.0])
        np.testing.assert_allclose(out.X[:, 0], [4.0, 3.0, 2.0, 1.0])
        # 入池外残留票：校验失败；重排后丢掉。
        orphan = FactorPanel(
            X=np.asarray([[1.0], [2.0], [3.0]], dtype=np.float32),
            y=np.asarray([1.0, 2.0, 3.0], dtype=np.float32),
            dates=["2024-01-01", "2024-01-01", "2024-01-01"],
            codes=["A", "B", "Z"],
            feature_names=["f0"],
        )
        self.assertFalse(_cache_panel_matches_pool(orphan, ["A", "B"]))
        trimmed = reorder_panel_by_codes(orphan, ["A", "B"])
        self.assertEqual(trimmed.codes, ["A", "B"])
        self.assertTrue(_cache_panel_matches_pool(trimmed, ["A", "B"]))
        none = reorder_panel_by_codes(orphan, ["MISSING"])
        self.assertEqual(none.n, 0)
        self.assertEqual(none.codes, [])

    def test_build_oo_factor_panel_uses_hub_assemble(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import build_oo_factor_panel

        x = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)
        y = np.asarray([0.1, 0.2], dtype=np.float64)
        with mock.patch(
            "core.research.oo_model_compact.assemble_oo_panels",
            return_value=(x, y, ["a", "b"], ["2024-01-02", "2024-01-03"], ["000001", "000002"]),
        ) as assem:
            panel = build_oo_factor_panel(
                {"000002": [{"date": "2024-01-01"}], "000001": [{"date": "2024-01-01"}]},
                horizon_days=1,
                use_cache=False,
            )
        assem.assert_called_once()
        kwargs = assem.call_args.kwargs
        self.assertTrue(kwargs.get("return_codes"))
        self.assertFalse(kwargs.get("aoo_core_only"))
        self.assertEqual(panel.n, 2)
        self.assertEqual(panel.codes, ["000001", "000002"])
        self.assertEqual(panel.feature_names, ["a", "b"])
        self.assertEqual(panel.X.dtype, np.float32)

    def test_pit_and_build(self):
        from core.research.yhat_transformer.seq_dataset import (
            assert_pit_last_bar_before_decision,
            build_seq_panel,
            samples_from_bars,
        )

        bars = _synth_bars(60)
        X, gap, y, dates = samples_from_bars("000001", bars, seq_len=10, horizon_days=1)
        self.assertGreater(len(dates), 5)
        self.assertEqual(X.shape[-1], 16)
        by_date = {str(b["date"])[:10]: j for j, b in enumerate(bars)}
        for d in dates[:5]:
            i = by_date[d]
            self.assertTrue(assert_pit_last_bar_before_decision(bars, i, 10))
        panel = build_seq_panel(
            {"000001": _synth_bars(50, 1), "000002": _synth_bars(50, 2)},
            head="oo",
            seq_len=8,
            horizon_days=1,
            use_cache=False,
        )
        self.assertGreater(panel.n, 10)
        self.assertEqual(panel.X.shape[-1], 16)

    def test_reversed_bars_match_chrono(self):
        from core.research.yhat_transformer.seq_dataset import samples_from_bars

        bars = _synth_bars(40, seed=4)
        X, _, _, dates = samples_from_bars("000001", bars, seq_len=8, horizon_days=1)
        Xr, _, _, dates_r = samples_from_bars(
            "000001", list(reversed(bars)), seq_len=8, horizon_days=1
        )
        self.assertEqual(dates, dates_r)
        np.testing.assert_allclose(X, Xr)
        self.assertEqual(dates, sorted(set(dates)))

    def test_factor_cache_key_tracks_prices(self):
        from core.research.yhat_transformer.factor_panel import _cache_key

        bars = _synth_bars(6, seed=2)
        base = _cache_key([("000001", bars)], horizon_days=1, kind="oo")
        revised = [dict(b) for b in bars]
        revised[2] = dict(revised[2])
        revised[2]["close"] = float(revised[2]["close"]) + 1.0
        self.assertNotEqual(
            _cache_key([("000001", revised)], horizon_days=1, kind="oo"),
            base,
        )
        self.assertNotEqual(
            _cache_key([("000001", bars)], horizon_days=0, kind="tc_compact"),
            _cache_key([("000001", bars)], horizon_days=0, kind="oo"),
        )

    def test_build_tc_factor_panel_uses_hub_compact(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import build_tc_factor_panel

        bars = {"000001": [{"date": "2024-01-02", "close": 1.0}], "000002": [{"date": "2024-01-03", "close": 2.0}]}
        x = np.asarray([[0.1, 1.0], [0.2, 2.0]], dtype=np.float64)
        metas = [
            {"stock_code": "000002", "date": "2024-01-03"},
            {"stock_code": "000001", "date": "2024-01-02"},
        ]

        def _collect(items, names, **kwargs):
            for it in items:
                it.pop("bars", None)
            self.assertEqual(kwargs.get("cache_head"), "tc")
            self.assertEqual(kwargs.get("tau_hm"), "open")
            self.assertTrue(kwargs.get("include_alpha_tc"))
            self.assertIn("z_τc_gap_pct", list(names))
            return (
                x,
                ["z_τc_gap_pct", "raw_atc_a"],
                [0.2, 0.1],
                ["2024-01-03", "2024-01-02"],
                metas,
                2,
            )

        with mock.patch(
            "core.research.panel_matrix.collect_tau_compact",
            side_effect=_collect,
        ) as col:
            panel = build_tc_factor_panel(bars, use_cache=False)
        col.assert_called_once()
        self.assertEqual(panel.codes, ["000001", "000002"])
        self.assertEqual(panel.dates, ["2024-01-02", "2024-01-03"])
        self.assertEqual(panel.X.dtype, np.float32)
        self.assertEqual(bars["000001"][0]["close"], 1.0)
        self.assertEqual(len(bars["000001"]), 1)

    def test_build_tc_factor_panel_rejects_misaligned_compact(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import build_tc_factor_panel

        x = np.ones((2, 1), dtype=np.float32)

        def _collect(items, names, **kwargs):
            return (x, ["z_τc_gap_pct"], [0.1, 0.2], ["2024-01-02"], [{"stock_code": "000001"}], 1)

        with mock.patch("core.research.panel_matrix.collect_tau_compact", side_effect=_collect):
            panel = build_tc_factor_panel(
                {"000001": [{"date": "2024-01-02", "close": 1.0}]},
                use_cache=False,
            )
        self.assertEqual(panel.n, 0)

    def test_cache_load_rejects_misaligned_rows(self):
        import os
        import tempfile

        from core.research.yhat_transformer.factor_panel import _try_load_cache

        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "bad.npz")
            np.savez_compressed(
                path,
                X=np.ones((2, 1), dtype=np.float32),
                y=np.ones((2,), dtype=np.float32),
                dates=np.asarray(["2024-01-02"], dtype=object),
                codes=np.asarray(["000001", "000002"], dtype=object),
                feature_names=np.asarray(["z_τc_gap_pct"], dtype=object),
            )
            self.assertIsNone(_try_load_cache(path))

    def test_seq8_vs_seq16(self):
        from core.research.yhat_transformer.seq_dataset import (
            SEQ16_FEATURE_NAMES,
            SEQ32_FEATURE_NAMES,
            SEQ64_FEATURE_NAMES,
            SEQ8_FEATURE_NAMES,
            samples_from_bars,
        )

        bars = _synth_bars(40, seed=3)
        x8, _, _, _ = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq8"
        )
        x16, _, _, d16 = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq16"
        )
        self.assertEqual(len(SEQ8_FEATURE_NAMES), 8)
        self.assertEqual(len(SEQ16_FEATURE_NAMES), 16)
        self.assertEqual(len(SEQ32_FEATURE_NAMES), 32)
        self.assertEqual(len(SEQ64_FEATURE_NAMES), 64)
        self.assertEqual(SEQ16_FEATURE_NAMES[:8], SEQ8_FEATURE_NAMES)
        self.assertEqual(SEQ32_FEATURE_NAMES[:16], SEQ16_FEATURE_NAMES)
        self.assertEqual(SEQ64_FEATURE_NAMES[:32], SEQ32_FEATURE_NAMES)
        self.assertGreater(x8.shape[0], 0)
        self.assertEqual(x8.shape[-1], 8)
        self.assertEqual(x16.shape[-1], 16)
        np.testing.assert_allclose(x8, x16[:, :, :8], rtol=0, atol=1e-5)
        self.assertTrue(np.all(np.isfinite(x16)))
        x32, _, _, d32 = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq32"
        )
        x64, _, _, d64 = samples_from_bars(
            "000001", bars, seq_len=8, feat_set="seq64"
        )
        self.assertEqual(x32.shape[-1], 32)
        self.assertEqual(x64.shape[-1], 64)
        self.assertGreater(x32.shape[0], 0)
        self.assertGreater(x64.shape[0], 0)
        # 嵌套前缀：更大档截断后等于更小档（同决策日）
        by16 = {d: i for i, d in enumerate(d16)}
        by32 = {d: i for i, d in enumerate(d32)}
        for i, d in enumerate(d32):
            np.testing.assert_allclose(x32[i, :, :16], x16[by16[d]], rtol=0, atol=1e-5)
        for i, d in enumerate(d64):
            np.testing.assert_allclose(x64[i, :, :32], x32[by32[d]], rtol=0, atol=1e-5)
        # 32/64 后半维不是前 16 的 lag 拷贝
        self.assertFalse(np.allclose(x32[:, :, 16:32], x32[:, :, :16]))
        self.assertFalse(np.allclose(x64[:, :, 32:48], x64[:, :, :16]))
        self.assertTrue(np.all(np.isfinite(x64)))
        # 各档样本量应一致（滚动不足时降级，不因 F 变大丢样本）
        self.assertEqual(x16.shape[0], x32.shape[0])
        self.assertEqual(x32.shape[0], x64.shape[0])

    def test_window_equals_vector_at(self):
        """窗口行必须等于当日 _vector_at，不能因为只算一次而改值。"""
        from core.research.yhat_transformer.seq_dataset import (
            _bar_ohlcv,
            _vector_at,
            samples_from_bars,
        )

        bars = _synth_bars(48, seed=7)
        L = 8
        X, _, _, dates = samples_from_bars(
            "000001", bars, seq_len=L, feat_set="seq64"
        )
        ohlcv = []
        vols = []
        for b in bars:
            got = _bar_ohlcv(b)
            ohlcv.append(got)
            vols.append(float(got[4]) if got is not None else 0.0)
        by_date = {str(b["date"])[:10]: j for j, b in enumerate(bars)}
        self.assertGreater(len(dates), 0)
        i = by_date[dates[0]]
        for k in range(L):
            vec = _vector_at(ohlcv, vols, i - L + k, 64)
            self.assertIsNotNone(vec)
            np.testing.assert_array_equal(X[0, k], vec)

    def test_parallel_panel_matches_serial_order(self):
        import os

        from core.research.yhat_transformer.seq_dataset import build_seq_panel

        bars = {f"{i:06d}": _synth_bars(40, seed=i) for i in range(16)}
        old = os.environ.get("SEQ_PANEL_WORKERS")
        try:
            os.environ["SEQ_PANEL_WORKERS"] = "1"
            serial = build_seq_panel(
                bars, head="oo", seq_len=8, feat_set="seq16", use_cache=False
            )
            os.environ["SEQ_PANEL_WORKERS"] = "4"
            parallel = build_seq_panel(
                bars, head="oo", seq_len=8, feat_set="seq16", use_cache=False
            )
        finally:
            if old is None:
                os.environ.pop("SEQ_PANEL_WORKERS", None)
            else:
                os.environ["SEQ_PANEL_WORKERS"] = old
        self.assertGreater(serial.n, 0)
        np.testing.assert_array_equal(serial.X, parallel.X)
        np.testing.assert_array_equal(serial.y, parallel.y)
        self.assertEqual(serial.codes, parallel.codes)
        self.assertEqual(serial.dates, parallel.dates)


class TestRankDayGroups(unittest.TestCase):
    def test_subsample_keeps_whole_days(self):
        from collections import Counter

        from core.research.yhat_transformer.train import (
            _prepare_rank_indices,
            _subsample_whole_groups,
        )

        dates = []
        for d in range(12):
            dates.extend([f"2024-01-{d + 1:02d}"] * 100)
        idx = list(range(len(dates)))
        kept = _subsample_whole_groups(dates, idx, max_rows=250, seed=0)
        counts = Counter(dates[i] for i in kept)
        self.assertTrue(counts)
        self.assertTrue(all(n == 100 for n in counts.values()))
        self.assertLessEqual(sum(counts.values()), 250)

        latest = _subsample_whole_groups(
            dates, idx, max_rows=250, seed=1, latest=True
        )
        last_days = sorted({dates[i] for i in latest})
        self.assertEqual(last_days[-1], "2024-01-12")

        tr, va = _prepare_rank_indices(
            dates, idx, max_train_rows=250, seed=0
        )
        tr_counts = Counter(dates[i] for i in tr)
        va_counts = Counter(dates[i] for i in va)
        self.assertTrue(all(n == 100 for n in tr_counts.values()))
        self.assertTrue(all(n == 100 for n in va_counts.values()))
        self.assertTrue(set(tr_counts).isdisjoint(va_counts))
        self.assertIn("2024-01-12", va_counts)
        # val 不被 cap//4 抽样：完整验证日组保留
        self.assertGreater(sum(va_counts.values()), 250 // 4)

    def test_default_val_is_last_tenth_of_train_days(self):
        from core.research.yhat_transformer.train import split_train_val_indices

        dates = []
        for d in range(20):
            dates.extend([f"2024-02-{d + 1:02d}"] * 5)
        idx = list(range(len(dates)))
        tr, va = split_train_val_indices(dates, idx)
        va_days = sorted({dates[i] for i in va})
        tr_days = sorted({dates[i] for i in tr})
        self.assertEqual(va_days, ["2024-02-19", "2024-02-20"])
        self.assertEqual(len(tr_days), 18)
        self.assertEqual(len(tr) + len(va), len(idx))
        # 显式 20% 仍可用；低于下限的请求钳回 10%（20 日 → 2 日）
        _, va20 = split_train_val_indices(dates, idx, val_frac=0.2)
        self.assertEqual(len({dates[i] for i in va20}), 4)
        _, va_lo = split_train_val_indices(dates, idx, val_frac=0.01)
        self.assertEqual(sorted({dates[i] for i in va_lo}), va_days)

    def test_subsample_stops_instead_of_taking_thinner_days(self):
        from unittest import mock

        from core.research.yhat_transformer.train import _subsample_whole_groups

        dates = ["2024-01-01"] * 40 + ["2024-01-02"] * 40 + ["2024-01-03"] * 10
        idx = list(range(len(dates)))

        class _Rng:
            def shuffle(self, order):
                order[:] = [0, 1, 2]

        with mock.patch(
            "core.research.yhat_transformer.train.np.random.RandomState",
            return_value=_Rng(),
        ):
            kept = _subsample_whole_groups(dates, idx, max_rows=50, seed=0)
        days = {dates[i] for i in kept}
        self.assertEqual(days, {"2024-01-01"})
        self.assertEqual(len(kept), 40)


def _has_torch() -> bool:
    try:
        import torch  # noqa: F401

        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_torch(), "torch missing")
class TestYhatModelSmoke(unittest.TestCase):
    def test_seq_factor_transformer_forward(self):
        import torch

        from core.research.yhat_transformer.model import (
            SeqFactorTransformer,
            SeqTransformer,
            count_parameters,
        )

        m = SeqTransformer(d_in=8, d_model=16, nhead=2, nlayers=1, dim_feedforward=32)
        self.assertGreater(count_parameters(m), 100)
        x = torch.randn(4, 8, 8)
        g = torch.randn(4)
        y = m(x, g)
        self.assertEqual(tuple(y.shape), (4,))
        model = SeqFactorTransformer(
            d_seq_in=8, d_factor=12, d_model=16, nhead=2, nlayers=1, factor_hidden=32
        )
        self.assertFalse(model.seq.with_head)
        self.assertFalse(hasattr(model.seq, "head"))
        out = model(x, g, torch.randn(4, 12))
        self.assertEqual(tuple(out.shape), (4,))
        with self.assertRaises(ValueError):
            SeqFactorTransformer(
                d_seq_in=8, d_factor=2, d_model=64, nhead=3, nlayers=1, factor_hidden=16
            )

    def test_rank_train_smoke(self):
        from core.research.yhat_transformer.seq_dataset import build_seq_panel
        from core.research.yhat_transformer.train import (
            predict_yhat_transformer,
            train_yhat_transformer,
        )

        stock = {f"{i:06d}": _synth_bars(40, seed=i) for i in range(1, 12)}
        panel = build_seq_panel(
            stock, head="oo_rank", seq_len=6, horizon_days=1, use_cache=False
        )
        self.assertGreater(panel.n, 30)
        idx = list(range(panel.n))
        state, report = train_yhat_transformer(
            panel,
            idx,
            d_model=16,
            nhead=2,
            nlayers=1,
            epochs=2,
            batch_size=64,
            seed=0,
            patience=2,
            loss="lambdarank",
        )
        self.assertTrue(report.get("success"))
        self.assertEqual(report.get("loss"), "lambdarank")
        preds = predict_yhat_transformer(
            state, panel, idx[:20], d_model=16, nhead=2, nlayers=1
        )
        self.assertEqual(preds.shape[0], 20)
        self.assertTrue(np.all(np.isfinite(preds)))

    def test_arm_t_holdout_each_epoch_does_not_pick_weights(self):
        from core.research.yhat_transformer.seq_dataset import SeqPanel
        from core.research.yhat_transformer.train import train_seq_factor_transformer

        rng = np.random.RandomState(0)
        dates: list = []
        codes: list = []
        for d in range(11):
            for c in range(6):
                dates.append(f"2024-01-{d + 1:02d}")
                codes.append(f"{c:06d}")
        n = len(dates)
        panel = SeqPanel(
            X=rng.randn(n, 4, 4).astype(np.float32),
            gap=rng.randn(n).astype(np.float32),
            y=rng.randn(n).astype(np.float32) * 2.0,
            dates=dates,
            codes=codes,
            seq_len=4,
            feature_names=("a", "b", "c", "d"),
        )
        fac = rng.randn(n, 3).astype(np.float32)
        train_idx = list(range(48))
        holdout_idx = np.asarray(list(range(48, n)), dtype=np.int64)
        _state, report = train_seq_factor_transformer(
            panel,
            fac,
            train_idx,
            d_model=8,
            nhead=2,
            nlayers=1,
            factor_hidden=8,
            epochs=2,
            batch_size=16,
            seed=0,
            patience=5,
            holdout_idx=holdout_idx,
        )
        rows = report.get("holdout_by_epoch") or []
        self.assertEqual([r["epoch"] for r in rows], [1, 2])
        self.assertTrue(all(int(r["n"]) == len(holdout_idx) for r in rows))
        self.assertTrue(all(r["cs_rank_ic"] is not None for r in rows))
        self.assertEqual(report.get("n_train", 0) + report.get("n_val", 0), len(train_idx))

    def test_configure_torch_threads_default_four(self):
        import os
        from unittest import mock

        import torch

        from core.research.yhat_transformer.train import _configure_torch_threads

        prev = torch.get_num_threads()
        try:
            with mock.patch.dict(os.environ, {"TORCH_NUM_THREADS": "4"}):
                self.assertEqual(_configure_torch_threads(), 4)
                self.assertEqual(torch.get_num_threads(), 4)
            with mock.patch.dict(os.environ, {"TORCH_NUM_THREADS": "1"}):
                self.assertEqual(_configure_torch_threads(), 1)
                self.assertEqual(torch.get_num_threads(), 1)
            with mock.patch.dict(os.environ, {"TORCH_NUM_THREADS": "nope"}):
                self.assertEqual(_configure_torch_threads(), 4)
                self.assertEqual(torch.get_num_threads(), 4)
        finally:
            torch.set_num_threads(max(1, int(prev)))

    def test_lambdarank_pairwise_pushes_inverted_head(self):
        import torch

        from core.research.yhat_transformer.train import (
            _lambdarank_group_loss,
            _resolve_loss,
        )

        self.assertEqual(_resolve_loss("lambdarank")[0], "lambdarank")
        y = torch.tensor([0.0, 1.0, 3.0, 8.0])
        aligned = torch.tensor([0.0, 1.0, 2.0, 3.0], requires_grad=True)
        flipped = torch.tensor([3.0, 2.0, 1.0, 0.0], requires_grad=True)
        loss_ok = _lambdarank_group_loss(aligned, y)
        loss_bad = _lambdarank_group_loss(flipped, y)
        self.assertLess(float(loss_ok.detach()), float(loss_bad.detach()))
        shifted = _lambdarank_group_loss(flipped.detach() + 5.0, y)
        self.assertTrue(torch.allclose(shifted, loss_bad.detach(), atol=1e-5))
        loss_bad.backward()
        self.assertIsNotNone(flipped.grad)
        # y 最高的名字当前分数最低，梯度应为负，下降才会把它抬上去。
        self.assertLess(float(flipped.grad[-1]), 0.0)
        self.assertGreater(float(flipped.grad[0]), 0.0)

        # 截断只留下当前第 1 名和其余名字的对。把第 1 名换成别的人，损失必须变。
        wide_y = torch.arange(6, dtype=torch.float32)
        top = torch.tensor([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
        swapped = top.clone()
        swapped[0], swapped[5] = top[5], top[0]
        full = _lambdarank_group_loss(swapped, wide_y, truncation=100)
        head = _lambdarank_group_loss(swapped, wide_y, truncation=1)
        self.assertGreater(float(full), 0.0)
        self.assertGreater(float(head), 0.0)
        self.assertFalse(torch.allclose(full, head, atol=1e-5))

        # 分数已经拉开的逆序，梯度应小于贴在一起的逆序。
        y3 = torch.tensor([0.0, 1.0, 2.0])
        close = torch.tensor([1.0, 0.0, 2.0], requires_grad=True)
        far = torch.tensor([50.0, -50.0, 2.0], requires_grad=True)
        _lambdarank_group_loss(close, y3).backward()
        _lambdarank_group_loss(far, y3).backward()
        self.assertLess(float(far.grad[0].abs()), float(close.grad[0].abs()))

    def test_mean_daily_ndcg_perfect_order(self):
        from core.research.yhat_transformer.metrics import mean_daily_ndcg

        metas = [{"date": "2024-01-02", "code": f"{i:06d}"} for i in range(4)]
        metas += [{"date": "2024-01-03", "code": f"{i:06d}"} for i in range(4)]
        ys = [0.0, 1.0, 2.0, 3.0, 0.0, 1.0, 2.0, 3.0]
        perfect = mean_daily_ndcg(ys, ys, metas, k=100)
        flipped = mean_daily_ndcg([3, 2, 1, 0, 3, 2, 1, 0], ys, metas, k=100)
        self.assertAlmostEqual(float(perfect), 1.0, places=6)
        self.assertLess(float(flipped), float(perfect))


class TestArmLLiveAligned(unittest.TestCase):
    def test_regression_calls_fit_oo_lgb_matrix(self):
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_l

        n, f = 12, 3
        dates = [f"2024-01-{(i // 3) + 2:02d}" for i in range(n)]
        fac = SimpleNamespace(
            X=np.random.RandomState(0).randn(n, f).astype(np.float64),
            y=np.linspace(-0.02, 0.02, n).astype(np.float64),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            feature_names=[f"f{j}" for j in range(f)],
            n=n,
        )
        blob = {
            "feature_names": list(fac.feature_names),
            "sample_count": 8,
        }
        train_idx = list(range(8))
        test_idx = list(range(8, 12))
        with mock.patch(
            "core.research.oo_lgb.fit_oo_lgb_matrix",
            return_value=(blob, {"sample_count": 8, "success": True}),
        ) as fit_m, mock.patch(
            "core.research.oo_lgb.predict_oo_lgb_matrix",
            return_value=[0.01, -0.02, 0.0, 0.03],
        ) as pred_m:
            out = _run_arm_l(
                fac, train_idx, test_idx, head_id="oo", rank=False
            )
        self.assertTrue(out.get("success"))
        self.assertTrue(out.get("live_aligned"))
        self.assertIn("L2 cube", str(out.get("note") or ""))
        self.assertEqual(out.get("max_train_cap"), 0)
        fit_m.assert_called_once()
        pred_m.assert_called_once()
        kw = fit_m.call_args.kwargs
        self.assertTrue(kw.get("cross_section_zscore"))
        self.assertEqual(list(kw.get("row_idx") or []), train_idx)
        cbs = kw.get("callbacks") or []
        self.assertEqual(len(cbs), 1)
        self.assertTrue(callable(cbs[0]))
        self.assertIsNone(out.get("theme_boost"))

    def test_tc_uses_theme_weights_and_hub_columns(self):
        from types import SimpleNamespace
        from unittest import mock

        from core.signal.factors.z_tc import Z_TC_MINUTE_KEYS
        from scripts.run_yhat_transformer_holdout import _run_arm_l

        minute = Z_TC_MINUTE_KEYS[0]
        n = 12
        # 截面列由 collect_tau_compact 写入，拟合只过滤，不再补广度。
        names = [
            "z_τc_gap_pct",
            minute,
            "raw_atc_x",
            "open_gap",
            "z_τc_theme_day",
            "z_τc_sector_gap_breadth",
            "z_τc_gap_vs_sector",
        ]
        x = np.ones((n, len(names)), dtype=np.float64)
        x[:, 0] = 0.1
        x[:6, 0] = 3.0
        x[:, 2] = np.nan
        x[:6, 2] = 0.2
        x[:, 4] = 0.0
        x[:6, 4] = 1.0
        x[:, 5] = 0.25
        x[:, 6] = 0.1
        dates = ["2024-01-02"] * 6 + ["2024-01-03"] * 6
        fac = SimpleNamespace(
            X=x,
            y=np.linspace(-0.02, 0.02, n),
            dates=dates,
            codes=[f"{i % 6:06d}" for i in range(n)],
            feature_names=names,
            n=n,
        )
        seen = {}

        def _fit(x_fit, y, feat, **kw):
            seen["feat"] = list(feat)
            w = kw.get("sample_weights")
            seen["w"] = [] if w is None else [float(v) for v in np.asarray(w)]
            return (
                {"feature_names": list(feat), "sample_count": 8},
                {"sample_count": 8, "success": True},
            )

        with mock.patch(
            "core.research.tc_lgb.fit_tc_lgb_matrix", side_effect=_fit
        ), mock.patch(
            "core.research.tc_lgb.predict_tc_lgb_matrix",
            return_value=[0.01, -0.02, 0.0, 0.03, 0.01, -0.01],
        ):
            out = _run_arm_l(
                fac, list(range(6)), list(range(6, 12)), head_id="tc", rank=False
            )
        self.assertTrue(out.get("success"))
        self.assertEqual(out.get("theme_boost"), 1.5)
        for key in (
            "z_τc_gap_pct",
            "z_τc_theme_day",
            "z_τc_sector_gap_breadth",
            "z_τc_gap_vs_sector",
            "raw_atc_x",
        ):
            self.assertIn(key, seen["feat"])
        self.assertNotIn(minute, seen["feat"])
        self.assertNotIn("open_gap", seen["feat"])
        self.assertEqual(seen["w"][0], 1.5)
        self.assertEqual(seen["w"][6], 1.0)

    def test_tc_hub_columns_rejects_width_mismatch(self):
        from scripts.run_yhat_transformer_holdout import (
            _tc_hub_columns,
            _theme_weights_from_panel,
        )

        x = np.ones((3, 1), dtype=np.float32)
        out, names = _tc_hub_columns(x, ["z_τc_gap_pct", "raw_atc_x"], [0])
        self.assertEqual(names, [])
        self.assertEqual(int(out.shape[1]), 0)
        self.assertIsNone(_theme_weights_from_panel(x, ["z_τc_theme_day", "z_τc_gap_pct"]))

    def test_tc_stops_before_lgb_when_memory_guard_trips(self):
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_l

        n = 12
        names = ["gap_pct", "raw_atc_x"]
        x = np.ones((n, len(names)), dtype=np.float32)
        x[:6, 0] = 3.0
        fac = SimpleNamespace(
            X=x,
            y=np.linspace(-0.02, 0.02, n),
            dates=["2024-01-02"] * 6 + ["2024-01-03"] * 6,
            codes=[f"{i % 6:06d}" for i in range(n)],
            feature_names=names,
            n=n,
        )
        with mock.patch(
            "core.research.horizon_l1.confirm_fit_memory_stop",
            return_value="内存保护：进程已用约 6.0 GB",
        ), mock.patch(
            "core.research.tc_lgb.fit_tc_lgb_matrix",
        ) as fit_m:
            out = _run_arm_l(
                fac, list(range(6)), list(range(6, 12)), head_id="tc", rank=False
            )
        self.assertFalse(out.get("success"))
        self.assertIn("内存保护", str(out.get("error") or ""))
        fit_m.assert_not_called()
        self.assertEqual(int(fac.X.shape[1]), 0)

    def test_theme_weights_skip_dict_per_row(self):
        from scripts.run_yhat_transformer_holdout import _theme_weights_from_panel

        col = np.array([[1.0], [0.0], [np.nan]], dtype=np.float32)
        weights = _theme_weights_from_panel(col, ["z_τc_theme_day"])
        self.assertEqual(list(weights), [1.5, 1.0, 1.0])
        self.assertIsNone(
            _theme_weights_from_panel(np.zeros((4, 1), dtype=np.float32), ["theme_day"])
        )
        self.assertIsNone(_theme_weights_from_panel(col, ["gap_pct"]))

    def test_raw_atc_finite_scans_train_rows_only(self):
        from core.research.panel_matrix import raw_atc_finite

        x = np.array(
            [
                [1.0, np.nan, 0.0],
                [np.nan, np.nan, 0.0],
                [np.nan, 2.0, 0.0],
            ],
            dtype=np.float32,
        )
        names = ["raw_atc_a", "raw_atc_b", "gap_pct"]
        self.assertEqual(raw_atc_finite(x, names, [0, 1]), ["raw_atc_a"])
        self.assertEqual(raw_atc_finite(x, names, [2]), ["raw_atc_b"])
        self.assertEqual(raw_atc_finite(x, names), ["raw_atc_a", "raw_atc_b"])

    def test_rank_calls_fit_lambdarank(self):
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_l

        n = 16
        dates = ["2024-01-02"] * 8 + ["2024-01-03"] * 8
        fac = SimpleNamespace(
            X=np.random.RandomState(0).randn(n, 3),
            y=np.linspace(-1, 1, n),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            feature_names=["a", "b", "c"],
            n=n,
        )
        with mock.patch(
            "core.research.oo_rank_lambdarank.fit_lambdarank",
            return_value={"success": True, "n_groups": 1, "sample_count": 8},
        ) as fit_m, mock.patch(
            "core.research.oo_rank_lambdarank._predict_oo_rank_matrix",
            return_value=[0.1] * 8,
        ):
            out = _run_arm_l(
                fac,
                list(range(8)),
                list(range(8, 16)),
                head_id="oo_rank",
                rank=True,
            )
        self.assertTrue(out.get("success"))
        self.assertIn("fit_lambdarank", str(out.get("note") or ""))
        self.assertEqual(out.get("n_groups"), 1)
        fit_m.assert_called_once()
        days = fit_m.call_args.args[0]
        self.assertEqual(len(days), 1)
        self.assertEqual(days[0].get("clock"), "open")
        self.assertEqual(fit_m.call_args.kwargs.get("feature_names"), ["a", "b", "c"])

    def test_arml_step_callback_prints(self):
        import io
        from contextlib import redirect_stdout
        from types import SimpleNamespace

        from scripts.run_yhat_transformer_holdout import _arml_boost_log_cb

        cb = _arml_boost_log_cb(period=30)
        env = SimpleNamespace(iteration=0, end_iteration=300)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cb(env)
            env.iteration = 1
            cb(env)
            env.iteration = 29
            cb(env)
        text = buf.getvalue()
        self.assertIn("ArmL step=1/300", text)
        self.assertIn("ArmL step=30/300", text)
        self.assertNotIn("ArmL step=2/300", text)

    def test_max_train_default_unlimited(self):
        import os
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _arm_l_max_train_rows

        self.assertEqual(_arm_l_max_train_rows(), 0)
        with mock.patch.dict(os.environ, {"YHAT_ARM_L_MAX_TRAIN": "0"}):
            self.assertEqual(_arm_l_max_train_rows(), 0)
        with mock.patch.dict(os.environ, {"YHAT_ARM_L_MAX_TRAIN": "8000"}):
            self.assertEqual(_arm_l_max_train_rows(), 8000)

    def test_drop_aligned_rows_without_cube_keeps_later_days(self):
        from unittest import mock

        from core.research.yhat_transformer.factor_panel import FactorPanel
        from core.research.yhat_transformer.seq_dataset import SeqPanel
        from scripts.run_yhat_transformer_holdout import (
            _drop_aligned_rows_without_cube,
        )

        dates = ["2022-06-22", "2022-07-01", "2022-06-22", "2022-07-04"]
        codes = ["000001", "000001", "000002", "000002"]
        seq = SeqPanel(
            X=np.arange(4 * 2 * 2, dtype=np.float32).reshape(4, 2, 2),
            gap=np.arange(4, dtype=np.float32),
            y=np.arange(4, dtype=np.float32),
            dates=dates,
            codes=codes,
            seq_len=2,
        )
        fac = FactorPanel(
            X=np.arange(8, dtype=np.float32).reshape(4, 2),
            y=np.arange(4, dtype=np.float32),
            dates=list(dates),
            codes=list(codes),
            feature_names=["a", "b"],
        )
        # 可读起点 07-01：06-22 切掉，当天及之后留下。
        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-07-01", 0),
        ):
            seq2, fac2 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertEqual(seq2.dates, ["2022-07-01", "2022-07-04"])
        self.assertEqual(fac2.codes, ["000001", "000002"])
        self.assertEqual(int(seq2.n), 2)
        self.assertEqual(int(fac2.n), 2)
        np.testing.assert_array_equal(fac2.X[:, 0], np.asarray([2, 6], dtype=np.float32))

        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-06-22", 0),
        ):
            seq3, fac3 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertIs(seq3, seq)
        self.assertIs(fac3, fac)

        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("", 0),
        ):
            seq4, fac4 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertIs(seq4, seq)
        self.assertEqual(seq4.dates, dates)
        self.assertIs(fac4, fac)

        # 起点之后的缺口留下：06-22 切掉，08-15 仍留在面板里。
        hole_dates = ["2022-06-22", "2022-07-01", "2022-08-15", "2022-08-16"]
        seq_h = SeqPanel(
            X=np.arange(4 * 2 * 2, dtype=np.float32).reshape(4, 2, 2),
            gap=np.arange(4, dtype=np.float32),
            y=np.arange(4, dtype=np.float32),
            dates=hole_dates,
            codes=list(codes),
            seq_len=2,
        )
        fac_h = FactorPanel(
            X=np.arange(8, dtype=np.float32).reshape(4, 2),
            y=np.arange(4, dtype=np.float32),
            dates=list(hole_dates),
            codes=list(codes),
            feature_names=["a", "b"],
        )
        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-07-01", 1),
        ):
            seq5, fac5 = _drop_aligned_rows_without_cube(seq_h, fac_h)
        self.assertEqual(seq5.dates, ["2022-07-01", "2022-08-15", "2022-08-16"])
        self.assertEqual(fac5.dates, ["2022-07-01", "2022-08-15", "2022-08-16"])

        with mock.patch(
            "core.research.materialize.cube_z.first_readable_cube_day",
            return_value=("2022-08-01", 0),
        ):
            seq6, fac6 = _drop_aligned_rows_without_cube(seq, fac)
        self.assertEqual(int(seq6.n), 0)
        self.assertEqual(int(fac6.n), 0)

    def test_first_readable_cube_day_skips_bad_prefix(self):
        from types import SimpleNamespace
        from unittest import mock

        from core.research.materialize.cube_z import first_readable_cube_day

        good = SimpleNamespace(
            names=["a"],
            z_mean=np.ones(1),
            z_std=np.ones(1),
        )
        thin = SimpleNamespace(
            names=["a"],
            z_mean=np.zeros(0),
            z_std=np.zeros(0),
        )

        def _load(day, _univ, **_kw):
            if day == "2022-06-01":
                raise OSError("truncated")
            if day == "2022-06-22":
                return thin
            if day == "2022-07-01":
                return good
            return None

        with mock.patch(
            "core.research.materialize.cube_z._cube_disk_span",
            return_value=("2022-06-01", "2022-06-22", "2022-07-01", "2022-08-15"),
        ), mock.patch(
            "core.research.materialize.cube_z.universe_codes",
            return_value=["000001", "000002"],
        ), mock.patch(
            "core.research.day_cube.load_day_cube",
            side_effect=_load,
        ):
            day, skipped = first_readable_cube_day("open")
        self.assertEqual(day, "2022-07-01")
        self.assertEqual(skipped, 2)

        with mock.patch(
            "core.research.materialize.cube_z._cube_disk_span",
            return_value=(),
        ):
            self.assertEqual(first_readable_cube_day("open"), ("", 0))

    def test_arm_t_uses_l2_cube_cs_z(self):
        """因子支路与 L 对齐：全面板走 cs_z_matrix_prefer_cube，不再列标准化。"""
        from types import SimpleNamespace
        from unittest import mock

        from scripts.run_yhat_transformer_holdout import _run_arm_t

        n, f, L = 20, 4, 6
        dates = [f"2024-01-{(i // 4) + 2:02d}" for i in range(n)]
        rng = np.random.RandomState(1)
        seq = SimpleNamespace(
            X=rng.randn(n, L, 8).astype(np.float32),
            gap=rng.randn(n).astype(np.float32),
            y=rng.randn(n).astype(np.float32),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            n=n,
        )
        fac = SimpleNamespace(
            X=rng.randn(n, f).astype(np.float64),
            y=seq.y.copy(),
            dates=dates,
            codes=list(seq.codes),
            feature_names=[f"f{j}" for j in range(f)],
            n=n,
        )
        z_out = (fac.X * 0.5).astype(np.float64)
        args = SimpleNamespace(
            d_model=16,
            nhead=2,
            nlayers=1,
            mlp_hidden=32,
            dropout=0.0,
            epochs=1,
            batch_size=8,
            lr=1e-3,
            device="cpu",
            patience=1,
            t_sample_frac=1.0,
            loss="smooth_l1",
        )
        fake_state = {"w": np.zeros(1)}
        fake_report = {
            "success": True,
            "n_params": 10,
            "Xseq": seq.X,
            "gap": seq.gap,
            "Xf": z_out.astype(np.float32),
            "y": seq.y,
            "dates": dates,
        }
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            return_value=z_out,
        ) as csz_m, mock.patch(
            "core.research.yhat_transformer.train.train_seq_factor_transformer",
            return_value=(fake_state, dict(fake_report)),
        ) as train_m, mock.patch(
            "core.research.yhat_transformer.train.predict_seq_factor_transformer",
            return_value=np.zeros(4, dtype=np.float64),
        ):
            out = _run_arm_t(
                seq, fac, list(range(16)), list(range(16, 20)), args
            )
        self.assertTrue(out.get("success"))
        self.assertIn("L2 cube CS-z", str(out.get("note") or ""))
        csz_m.assert_called_once()
        csz_kw = csz_m.call_args
        self.assertEqual(csz_kw.kwargs.get("clock"), "open")
        self.assertEqual(list(csz_kw.args[1]), fac.feature_names)
        train_m.assert_called_once()
        fac_passed = train_m.call_args.args[1]
        self.assertEqual(fac_passed.dtype, np.float32)
        np.testing.assert_allclose(fac_passed, z_out.astype(np.float32), atol=1e-5)
        # 非有限值应填 0（与 rank L 后处理一致）
        z_nan = z_out.copy()
        z_nan[0, 0] = np.nan
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            return_value=z_nan,
        ), mock.patch(
            "core.research.yhat_transformer.train.train_seq_factor_transformer",
            return_value=(fake_state, dict(fake_report)),
        ) as train_nan, mock.patch(
            "core.research.yhat_transformer.train.predict_seq_factor_transformer",
            return_value=np.zeros(4, dtype=np.float64),
        ):
            fac2 = SimpleNamespace(
                X=rng.randn(n, f).astype(np.float64),
                y=seq.y.copy(),
                dates=dates,
                codes=list(seq.codes),
                feature_names=[f"f{j}" for j in range(f)],
                n=n,
            )
            _run_arm_t(seq, fac2, list(range(16)), list(range(16, 20)), args)
        fac_nan = train_nan.call_args.args[1]
        self.assertTrue(np.isfinite(fac_nan).all())
        self.assertEqual(float(fac_nan[0, 0]), 0.0)

    def test_arm_t_cs_z_materialize_required(self):
        from types import SimpleNamespace
        from unittest import mock

        from core.research.materialize.consumer import MaterializeRequired
        from scripts.run_yhat_transformer_holdout import _run_arm_t

        n, f = 8, 2
        dates = ["2024-01-02"] * 4 + ["2024-01-03"] * 4
        fac = SimpleNamespace(
            X=np.ones((n, f), dtype=np.float64),
            y=np.zeros(n, dtype=np.float64),
            dates=dates,
            codes=[f"{i:06d}" for i in range(n)],
            feature_names=["a", "b"],
            n=n,
        )
        seq = SimpleNamespace(
            X=np.zeros((n, 4, 8), dtype=np.float32),
            gap=np.zeros(n, dtype=np.float32),
            y=np.zeros(n, dtype=np.float32),
            dates=dates,
            codes=list(fac.codes),
            n=n,
        )
        args = SimpleNamespace(
            d_model=8,
            nhead=2,
            nlayers=1,
            mlp_hidden=8,
            dropout=0.0,
            epochs=1,
            batch_size=4,
            lr=1e-3,
            device="cpu",
            patience=1,
            t_sample_frac=1.0,
            loss="smooth_l1",
        )
        with mock.patch(
            "core.research.materialize.cube_z.cs_z_matrix_prefer_cube",
            side_effect=MaterializeRequired({"error": "缺 L2"}),
        ), mock.patch(
            "core.research.yhat_transformer.train.train_seq_factor_transformer"
        ) as train_m:
            out = _run_arm_t(seq, fac, list(range(4)), list(range(4, 8)), args)
        self.assertFalse(out.get("success"))
        self.assertIn("MaterializeRequired", str(out.get("error") or ""))
        train_m.assert_not_called()


class TestRelativeHardGates(unittest.TestCase):
    def test_single_arm_factory_returns_none(self):
        from scripts.run_yhat_transformer_holdout import _relative_hard_gates

        summary = {"arm_L": {"success": True, "cs_rank_ic": 0.04}}
        called = {"n": 0}

        def _gate(_base, _pack):
            called["n"] += 1
            return {"pass": True}

        self.assertIsNone(_relative_hard_gates(summary, _gate, factory="L"))
        self.assertIsNone(_relative_hard_gates(summary, _gate, factory="T"))
        self.assertEqual(called["n"], 0)

    def test_lt_missing_baseline_is_fail_not_crash(self):
        from scripts.run_yhat_transformer_holdout import _relative_hard_gates

        summary = {
            "arm_L": {"success": False, "error": "boom"},
            "arm_T": {"success": True, "cs_rank_ic": 0.02},
        }
        gates = _relative_hard_gates(summary, lambda _a, _b: {"pass": True}, factory="LT")
        self.assertIsInstance(gates, dict)
        self.assertFalse(gates["T"]["pass"])
        self.assertIn("baseline missing", gates["T"]["reasons"][0])


class TestHoldoutSystemExitDesk(unittest.TestCase):
    def test_sigterm_systemexit_marks_desk_failed(self):
        """SIGTERM → SystemExit(143) 时回写 desk，避免 job 假 running。"""
        from unittest import mock

        import scripts.run_yhat_transformer_holdout as holdout

        marked = {}

        def _fake_parse():
            ns = mock.Mock()
            ns.head = "oo"
            ns.factory = "L"
            ns.update_desk = True
            return ns

        with mock.patch.object(
            holdout.argparse.ArgumentParser, "parse_args", return_value=_fake_parse()
        ), mock.patch.object(
            holdout, "_run_holdout", side_effect=SystemExit(143)
        ), mock.patch.object(
            holdout,
            "_desk_mark_failed",
            side_effect=lambda *a, **k: marked.update({"args": a, **k}),
        ):
            with self.assertRaises(SystemExit) as cm:
                holdout.main()
            self.assertEqual(cm.exception.code, 143)
        self.assertEqual(marked["args"][0], "oo")
        self.assertIn("SIGTERM", marked["args"][1])
        self.assertEqual(marked.get("factory"), "L")

    def test_zero_systemexit_does_not_mark_failed(self):
        from unittest import mock

        import scripts.run_yhat_transformer_holdout as holdout

        calls = []

        def _fake_parse():
            ns = mock.Mock()
            ns.head = "oo"
            ns.factory = "T"
            ns.update_desk = True
            return ns

        with mock.patch.object(
            holdout.argparse.ArgumentParser, "parse_args", return_value=_fake_parse()
        ), mock.patch.object(
            holdout, "_run_holdout", side_effect=SystemExit(0)
        ), mock.patch.object(
            holdout, "_desk_mark_failed", side_effect=lambda *a, **k: calls.append((a, k))
        ):
            with self.assertRaises(SystemExit):
                holdout.main()
        self.assertEqual(calls, [])


class TestFactorPanelBuildLine(unittest.TestCase):
    def test_arm_t_only_names_the_slice_not_arm_l(self):
        from scripts.run_yhat_transformer_holdout import _factor_panel_build_line

        oo = _factor_panel_build_line("oo", ["T"])
        self.assertIn("head=oo", oo)
        self.assertIn("先建 aoo_full", oo)
        self.assertIn("裁成 aoo_core", oo)
        self.assertNotIn("Arm·L", oo)
        rank = _factor_panel_build_line("oo_rank", ["T"])
        self.assertIn("先建 aoo_full", rank)
        self.assertIn("裁成 aoo_core", rank)
        full = _factor_panel_build_line("oo", ["T"], full_aoo=True)
        self.assertIn("Arm·T 用 aoo_full", full)
        self.assertNotIn("Arm·L", full)
        tc = _factor_panel_build_line("tc", ["T"], atc_core=True)
        self.assertIn("先建 atc_full", tc)
        self.assertIn("裁成 atc_core", tc)
        self.assertNotIn("Arm·L", tc)
        tc_full = _factor_panel_build_line("tc", ["T"])
        self.assertIn("Arm·T 用 atc_full", tc_full)
        self.assertNotIn("裁成", tc_full)

    def test_arm_l_and_both_keep_the_full_source(self):
        from scripts.run_yhat_transformer_holdout import _factor_panel_build_line

        lone = _factor_panel_build_line("oo", ["L"])
        self.assertIn("Arm·L 用 aoo_full", lone)
        self.assertNotIn("Arm·T", lone)
        both = _factor_panel_build_line("oo", ["L", "T"])
        self.assertIn("Arm·L 用 aoo_full", both)
        self.assertIn("裁成 aoo_core", both)
        same = _factor_panel_build_line("oo", ["L", "T"], full_aoo=True)
        self.assertIn("Arm·L 与 Arm·T 都用 aoo_full", same)
        tc_both = _factor_panel_build_line("tc_rank", ["L", "T"], atc_core=True)
        self.assertIn("Arm·L 用 atc_full", tc_both)
        self.assertIn("裁成 atc_core", tc_both)


class TestLabelHorizonByHead(unittest.TestCase):
    def test_tc_embargo_stays_zero_when_cli_horizon_is_one(self):
        import datetime

        from core.research.holdout import resolve_ridge_split
        from scripts.run_yhat_transformer_holdout import label_horizon_days_for_head

        self.assertEqual(label_horizon_days_for_head("tc", 1), 0)
        self.assertEqual(label_horizon_days_for_head("tc_rank", 1), 0)
        self.assertEqual(label_horizon_days_for_head("oo", 1), 1)
        self.assertEqual(label_horizon_days_for_head("oo_rank", 3), 3)

        dates = []
        day = datetime.date(2026, 1, 5)
        while len(dates) < 40:
            if day.weekday() < 5:
                dates.append(day.isoformat())
            day += datetime.timedelta(days=1)
        _, _, same_day = resolve_ridge_split(
            dates,
            holdout_trading_days=5,
            label_horizon_days=label_horizon_days_for_head("tc", 1),
            calendar_dates=dates,
        )
        _, _, overnight = resolve_ridge_split(
            dates,
            holdout_trading_days=5,
            label_horizon_days=label_horizon_days_for_head("oo", 1),
            calendar_dates=dates,
        )
        self.assertEqual(same_day["eval_start"], overnight["eval_start"])
        self.assertEqual(same_day["label_horizon_days"], 0)
        eval_i = dates.index(same_day["eval_start"])
        self.assertEqual(same_day["fit_end"], dates[eval_i - 1])
        self.assertEqual(overnight["fit_end"], dates[eval_i - 2])


if __name__ == "__main__":
    unittest.main()